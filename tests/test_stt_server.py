"""
Tests for STT Server
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# Add project path
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))


def _tone() -> bytes:
    """一秒 220 Hz 正弦(int16 PCM)。全零会被静音闸门挡下、根本不跑模型。"""
    t = np.arange(16000) / 16000
    return (3000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()


class TestTranscriptionResult:
    """Test TranscriptionResult model"""

    def test_basic_result(self):
        """Test basic result creation"""
        from services.stt_server import TranscriptionResult

        result = TranscriptionResult(text="Hello world")
        assert result.text == "Hello world"
        assert result.confidence == 1.0
        assert result.language == "auto"
        assert result.is_final is True


class TestSTTEngine:
    """Test STTEngine class"""

    def test_init(self):
        """Test engine initialization"""
        from services.stt_server import STTEngine

        from shared.model_registry import get_default_model

        engine = STTEngine()
        # 以默认值的唯一来源为准。这里以前写死了非 Apple 平台是 whisper_turbo,
        # 73e141d 把兜底改成 whisper_base 之后只有非 Mac 的 CI 会挂。
        assert engine.default_model == get_default_model()
        assert not engine._is_loaded
        assert not engine._loading

    def test_available_models(self):
        """Test available models configuration"""
        from services.stt_server import STTEngine

        assert "qwen_asr_mlx_native_small" in STTEngine.AVAILABLE_MODELS
        assert "model_id" in STTEngine.AVAILABLE_MODELS["qwen_asr_mlx_native_small"]

    def test_is_loading(self):
        """Test loading state"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        assert not engine.is_loading()

        engine._loading = True
        assert engine.is_loading()

    def test_is_model_loaded(self):
        """Test model loaded state"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        assert not engine.is_model_loaded()

        engine._is_loaded = True
        assert engine.is_model_loaded()

    def test_get_stats(self):
        """Test statistics"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        stats = engine.get_stats()
        assert "total_requests" in stats
        assert "failed_requests" in stats
        assert "active_connections" in stats
        assert stats["total_requests"] == 0
        assert stats["failed_requests"] == 0

    def test_connection_management(self):
        """Test connection counter"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        assert engine._active_connections == 0

        engine.increment_connections()
        assert engine._active_connections == 1

        engine.increment_connections()
        assert engine._active_connections == 2

        engine.decrement_connections()
        assert engine._active_connections == 1

        # Test that it doesn't go negative
        engine.decrement_connections()
        engine.decrement_connections()
        assert engine._active_connections == 0

    @pytest.mark.asyncio
    async def test_load_returns_true_when_already_loaded(self):
        """Test that load returns quickly if already loaded"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        engine._is_loaded = True

        result = await engine.load()
        assert result is True

    @pytest.mark.asyncio
    async def test_load_failure_is_reported_not_stuck_loading(self, monkeypatch):
        """加载失败要留下原因,/health 报 error,而不是永远停在 loading"""
        from fastapi.testclient import TestClient

        import services.stt_server as srv
        from services.stt_server import STTEngine

        engine = STTEngine()

        def boom():
            raise ModuleNotFoundError("No module named 'mlx_whisper'")

        monkeypatch.setattr(engine, "_load_model_sync", boom)
        assert await engine.load() is False
        assert "mlx_whisper" in engine.load_error()

        monkeypatch.setattr(srv, "engine", engine)
        body = TestClient(srv.app).get("/health").json()
        assert body["status"] == "error"
        assert "mlx_whisper" in body["error"]

        # 重新加载期间不再报上一次的失败原因
        engine._load_error = "stale"
        engine._loading = True
        assert engine.load_error() is None

    @staticmethod
    async def _settle(engine, timeout=5.0):
        """等后台加载 / 回退跑完。"""
        import asyncio

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        await asyncio.sleep(0)
        while loop.time() < deadline:
            await asyncio.sleep(0.02)
            if not engine.is_loading() and (engine.is_model_loaded() or engine._load_error):
                return

    @pytest.mark.asyncio
    async def test_switch_persists_only_after_load(self, monkeypatch):
        """模型选择只在加载成功之后才持久化(R5)"""
        from services.stt_server import STTEngine

        engine = STTEngine(default_model="whisper_tiny")
        engine._is_loaded = True
        engine._model = object()
        monkeypatch.setattr(engine, "_load_model_sync", lambda: setattr(engine, "_model", object()))

        persisted = []
        result = await engine.switch_model("whisper_base", on_loaded=persisted.append)
        assert result["is_loading"] is True
        assert persisted == []  # 还没加载完,不能先记下来
        await self._settle(engine)
        assert engine.is_model_loaded()
        assert persisted == ["whisper_base"]

    @pytest.mark.asyncio
    async def test_switch_failure_rolls_back_and_keeps_reason(self, monkeypatch):
        """切到坏模型:回退到原来的模型,不持久化,失败原因能查到(R5)"""
        from services.stt_server import STTEngine

        engine = STTEngine(default_model="whisper_tiny")
        engine._is_loaded = True
        engine._model = object()

        def load():
            if engine.current_model_name == "whisper_small":
                raise OSError("download interrupted")
            engine._model = object()

        monkeypatch.setattr(engine, "_load_model_sync", load)
        persisted = []
        await engine.switch_model("whisper_small", on_loaded=persisted.append)
        await self._settle(engine)
        # 第一次 settle 可能停在「新模型失败」那一刻,再等回退完成
        await self._settle(engine)

        assert engine.current_model_name == "whisper_tiny"
        assert engine.is_model_loaded()
        assert persisted == []
        assert "download interrupted" in engine.switch_error("whisper_small")

    @pytest.mark.asyncio
    async def test_transcribe_error_carries_load_reason(self, monkeypatch):
        """模型加载失败时,转写报错要带上真正的原因(R11)"""
        from services.stt_server import STTEngine

        engine = STTEngine(default_model="whisper_tiny")

        def boom():
            raise ModuleNotFoundError("No module named 'transformers'")

        monkeypatch.setattr(engine, "_load_model_sync", boom)
        with pytest.raises(RuntimeError, match="transformers"):
            await engine.transcribe(np.zeros(1600, dtype=np.int16).tobytes())

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="MLX model loads successfully by default")
    async def test_transcribe_raises_when_load_fails(self):
        """Test that transcribe raises error when model load fails (requires broken config)"""
        from services.stt_server import STTEngine

        engine = STTEngine(default_model="invalid_model_name")
        with pytest.raises(RuntimeError, match="加载失败"):
            await engine.transcribe(b"fake audio")

    @pytest.mark.asyncio
    async def test_transcribe_whisper_mlx_returns_result(self, monkeypatch):
        """whisper_mlx 分支必须返回 TranscriptionResult 而非元组(H2)"""
        from services.stt_server import STTEngine, TranscriptionResult

        engine = STTEngine()
        engine._is_loaded = True
        engine._model_type = "whisper_mlx"
        engine._model = {"model_id": "mock"}

        # 注入 fake mlx_whisper 模块,避免真实依赖
        import sys
        import types

        fake_mlx_whisper = types.ModuleType("mlx_whisper")
        seen = {}

        def fake_transcribe(*a, **k):
            seen.update(k)
            return {"text": "hello", "language": "en"}

        fake_mlx_whisper.transcribe = fake_transcribe
        monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx_whisper)

        audio = _tone()
        result = await engine.transcribe(audio)
        assert isinstance(result, TranscriptionResult)
        assert result.text == "hello"
        # mlx_whisper 不认 return_timestamps(那是 transformers 的参数),传了必抛
        # TypeError —— whisper_mlx* 以前一句都转写不出来(R40)
        assert "return_timestamps" not in seen

    @pytest.mark.asyncio
    async def test_transcribe_whisper_cpp_returns_result(self, monkeypatch):
        """whisper_cpp 分支必须返回 TranscriptionResult 且无 asyncio.run 嵌套(H2/H3)"""
        from services.stt_server import STTEngine, TranscriptionResult

        engine = STTEngine()
        engine._is_loaded = True
        engine._model_type = "whisper_cpp"

        class FakeWhisperCpp:
            async def transcribe(self, audio_data, language="auto", sample_rate=16000):
                return TranscriptionResult(text="cpp result", language="en")

        engine._model = FakeWhisperCpp()

        audio = _tone()
        result = await engine.transcribe(audio)
        assert isinstance(result, TranscriptionResult)
        assert result.text == "cpp result"

    @pytest.mark.asyncio
    async def test_transcribe_whisper_turbo_returns_result(self):
        """whisper_turbo 分支必须返回 TranscriptionResult 而非元组(H2)"""
        from services.stt_server import STTEngine, TranscriptionResult

        engine = STTEngine()
        engine._is_loaded = True
        engine._model_type = "whisper_turbo"

        seen = {}

        class FakeTurbo:
            def __call__(self, audio, return_timestamps=False, generate_kwargs=None):
                seen["return_timestamps"] = return_timestamps
                return {"text": "turbo result"}

        engine._model = FakeTurbo()

        audio = _tone()
        result = await engine.transcribe(audio)
        assert isinstance(result, TranscriptionResult)
        assert result.text == "turbo result"
        # 超过 30 秒的音频必须开时间戳,否则 transformers 直接报错(R34)
        assert seen["return_timestamps"] is True

    @pytest.mark.asyncio
    async def test_transcribe_qwen_transformers_returns_result(self):
        """qwen transformers 分支必须返回 TranscriptionResult 而非元组(H2)"""
        from services.stt_server import STTEngine, TranscriptionResult

        engine = STTEngine()
        engine._is_loaded = True
        engine._model_type = "qwen_transformers"

        class FakeResult:
            text = "qwen result"
            language = "zh"

        class FakeQwen:
            def transcribe(self, audio, language=None):
                return [FakeResult()]

        engine._model = FakeQwen()

        audio = _tone()
        result = await engine.transcribe(audio)
        assert isinstance(result, TranscriptionResult)
        assert result.text == "qwen result"
        assert result.language == "zh"


class TestStructuredLogging:
    """Test structured logging"""

    def test_log_formatter(self):
        """Test StructuredLogFormatter"""
        import json
        import logging

        from services.stt_server import StructuredLogFormatter

        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert "timestamp" in data
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["message"] == "Test message"


class TestModels:
    """Test model classes"""

    def test_model_info(self):
        """Test ModelInfo model"""
        from services.stt_server import ModelInfo

        info = ModelInfo(
            name="qwen_asr_mlx_native_small",
            description="Test model",
            is_loaded=True,
            is_default=True,
        )
        assert info.name == "qwen_asr_mlx_native_small"
        assert info.is_loaded is True

    def test_health_status(self):
        """Test HealthStatus model"""
        from services.stt_server import HealthStatus

        health = HealthStatus(
            status="ok",
            uptime_seconds=100.0,
            current_model="qwen_asr_mlx_native_small",
            loaded_models=["qwen_asr_mlx_native_small"],
            active_connections=2,
            total_requests=10,
            failed_requests=1,
        )
        assert health.status == "ok"
        assert health.total_requests == 10

    def test_error_response(self):
        """Test ErrorResponse model"""
        from services.stt_server import ErrorResponse

        error = ErrorResponse(
            error_code="E5001",
            error_message="Test error",
        )
        assert error.error_code == "E5001"
        assert error.error_message == "Test error"
        assert "error_code" in error.to_dict()


class TestLLMProxyError:
    """_llm_error:转发失败必须带非 2xx 状态码"""

    def _body(self, response):
        import json

        return json.loads(bytes(response.body))

    def test_default_status_is_bad_gateway(self):
        """默认 502:调用方只看状态码也不会把失败当成功(回归)"""
        from services.stt_server import _llm_error

        resp = _llm_error("LLM 不可达")
        assert resp.status_code == 502
        body = self._body(resp)
        assert body["error_code"] == "LLM_PROXY_ERROR"
        assert body["error_message"] == "LLM 不可达"
        assert "details" in body

    def test_upstream_status_is_passed_through(self):
        """上游给了状态码就原样带回去"""
        from services.stt_server import _llm_error

        assert _llm_error("模型加载失败", 503).status_code == 503

    def test_upstream_message_extracted(self):
        """从上游失败响应里挖出可读原因"""
        import httpx

        from services.stt_server import _upstream_message

        resp = httpx.Response(503, json={"status": "failed", "message": "模型 X 加载失败"})
        assert _upstream_message(resp) == "模型 X 加载失败"

    def test_upstream_message_falls_back_to_status(self):
        """上游没给消息时退回状态码,不能返回空串"""
        import httpx

        from services.stt_server import _upstream_message

        resp = httpx.Response(500, text="boom")
        assert "500" in _upstream_message(resp)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "outcome, want",
        [
            ("connect", "连不上 LLM 服务"),
            ("timeout", "没有应答"),
            ((503, {"detail": "LLM 模型没有加载成功:OOM"}), "OOM"),
            (
                (200, {"text": "原文", "success": False, "error": "LLM 输出达到长度上限"}),
                "长度上限",
            ),
            ((200, {"text": "整理后。", "success": True, "llm_latency_ms": 12}), None),
        ],
    )
    async def test_llm_failure_is_reported_not_swallowed(self, monkeypatch, outcome, want):
        """LLM 后处理失败时退回原文,但要把原因带回来(R8)"""
        import httpx

        import services.stt_server as srv

        async def fake_post(self, *a, **k):
            if outcome == "connect":
                raise httpx.ConnectError("refused")
            if outcome == "timeout":
                raise httpx.ReadTimeout("slow")
            status, body = outcome
            return httpx.Response(status, json=body)

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        text, _latency, err = await srv.call_llm_server("原文")
        if want is None:
            assert err is None and text == "整理后。"
        else:
            assert want in err
            assert text == "原文"

    def test_ws_result_carries_llm_error(self, monkeypatch):
        """WS 的 result 消息带 llm_error,客户端才知道这次没经过后处理(R8)"""
        import base64
        import json

        from fastapi.testclient import TestClient

        import services.stt_server as srv
        from services.stt_server import STTEngine, TranscriptionResult

        engine = STTEngine()

        async def fake_transcribe(audio, language="auto", context=None):
            return TranscriptionResult(text="嗯那个明天开会", language="zh")

        async def fake_llm(text, request_id="", vocabulary_hint=None, lang="zh"):
            return text, 0, "连不上 LLM 服务(可能没有启动)"

        monkeypatch.setattr(engine, "transcribe", fake_transcribe)
        monkeypatch.setattr(srv, "engine", engine)
        monkeypatch.setattr(srv, "call_llm_server", fake_llm)
        # 非 Apple 平台(CI)上 LLM 后处理默认不可用,这里要测的是「能用但失败了」
        monkeypatch.setattr(srv, "LLM_SUPPORTED", True)

        for enabled, want in [(True, "连不上 LLM 服务(可能没有启动)"), (False, None)]:
            monkeypatch.setattr(srv, "LLM_ENABLED", enabled)
            with TestClient(srv.app).websocket_connect("/ws/stream") as ws:
                assert json.loads(ws.receive_text())["type"] == "ready"
                audio = base64.b64encode(b"\x01\x00" * 1600).decode()
                ws.send_text(json.dumps({"type": "audio", "data": audio}))
                ws.send_text(json.dumps({"type": "end"}))
                while True:
                    msg = json.loads(ws.receive_text())
                    if msg["type"] == "result":
                        break
                    assert msg["type"] in ("stt_result", "llm_start"), msg
            assert msg["text"] == "嗯那个明天开会"
            assert "llm_error" in msg
            assert msg["llm_error"] == want

    def test_ws_ready_does_not_wait_for_llm_health(self, monkeypatch):
        """ready 消息不再先同步问一次 LLM /health(R29):LLM 卡住也不拖慢每句话"""
        import asyncio
        import json
        import time

        from fastapi.testclient import TestClient

        import services.stt_server as srv

        calls = []

        async def slow_refresh():
            calls.append(1)
            await asyncio.sleep(2)  # 模拟卡住的 LLM
            srv._llm_model_cache["refreshing"] = False

        monkeypatch.setattr(srv, "_refresh_llm_model", slow_refresh)
        monkeypatch.setattr(srv, "LLM_SUPPORTED", True)
        monkeypatch.setattr(srv, "LLM_ENABLED", True)
        monkeypatch.setitem(srv._llm_model_cache, "model", "Qwen3.5-2B-OptiQ")
        monkeypatch.setitem(srv._llm_model_cache, "at", float("-inf"))
        monkeypatch.setitem(srv._llm_model_cache, "refreshing", False)

        started = time.monotonic()
        with TestClient(srv.app).websocket_connect("/ws/stream") as ws:
            ready = json.loads(ws.receive_text())
            elapsed = time.monotonic() - started
            ws.send_text(json.dumps({"type": "end"}))
        assert ready["type"] == "ready"
        assert ready["llm_enabled"] is True
        assert ready["llm_model"] == "Qwen3.5-2B-OptiQ"  # 字段照旧,用缓存的值
        assert elapsed < 1.5, f"ready 等了 {elapsed:.1f} 秒"
        assert calls == [1]  # 缓存过期时在后台刷新一次

    def test_unsupported_platform_cannot_enable_llm(self, monkeypatch):
        """非 Apple 平台:开关报不支持和原因,打开返回 409,不落盘(F17)"""
        from fastapi.testclient import TestClient

        import services.stt_server as srv

        monkeypatch.setattr(srv, "LLM_SUPPORTED", False)
        monkeypatch.setattr(srv, "LLM_UNSUPPORTED_REASON", "只支持 Apple Silicon")
        monkeypatch.setattr(srv, "LLM_ENABLED", True)
        monkeypatch.setattr(srv, "save_state", lambda state: pytest.fail("不该持久化"))
        client = TestClient(srv.app)

        body = client.get("/llm/enabled").json()
        assert body == {"enabled": False, "supported": False, "reason": "只支持 Apple Silicon"}
        assert srv.llm_active() is False  # 转写时也不再白走一趟 LLM

        r = client.put("/llm/enabled", json={"enabled": True})
        assert r.status_code == 409
        assert r.json()["error_message"] == "只支持 Apple Silicon"

    def test_supported_platform_reports_no_reason(self, monkeypatch):
        from fastapi.testclient import TestClient

        import services.stt_server as srv

        monkeypatch.setattr(srv, "LLM_SUPPORTED", True)
        monkeypatch.setattr(srv, "LLM_ENABLED", True)
        body = TestClient(srv.app).get("/llm/enabled").json()
        assert body == {"enabled": True, "supported": True, "reason": None}

    def test_llm_switch_timeout_says_still_loading(self, monkeypatch):
        """转发切换超时 ≠ 切换失败:LLM 还在后台加载,完成后会自己生效(R16)"""
        import httpx
        from fastapi.testclient import TestClient

        import services.stt_server as srv

        async def slow_post(self, *a, **k):
            raise httpx.ReadTimeout("timed out")

        monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)
        monkeypatch.setattr(srv, "save_state", lambda state: pytest.fail("超时不该持久化"))
        r = TestClient(srv.app).post("/llm/models/select", json={"model_name": "Qwen3.5-4B-MLX"})
        assert r.status_code == 504
        assert "还在加载 Qwen3.5-4B-MLX" in r.json()["error_message"]


class TestTranscriptionRequest:
    """Test TranscriptionRequest model"""

    def test_defaults(self):
        """Test default values"""
        from services.stt_server import TranscriptionRequest

        req = TranscriptionRequest()
        assert req.language == "auto"

    def test_custom_values(self):
        """Test custom values"""
        from services.stt_server import TranscriptionRequest

        req = TranscriptionRequest(language="zh")
        assert req.language == "zh"


# Integration tests (require actual model loading)
@pytest.mark.integration
class TestSTTEngineIntegration:
    """Integration tests for STTEngine"""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires model download and MLX GPU")
    async def test_actual_load(self):
        """Test actual model loading from services.stt_server (requires MLX)"""
        from services.stt_server import STTEngine

        engine = STTEngine(default_model="qwen_asr_mlx_native_small")
        result = await engine.load()
        assert result is True
        assert engine._is_loaded


class TestSilenceAndHallucination:
    """静音不跑模型、公认的幻觉句被挡掉(R25,实测 Qwen 把 3 秒静音识别成 "The.")"""

    def test_silence_is_detected(self):
        from services.stt_engine import is_silent

        assert is_silent(np.zeros(16000, dtype=np.float32))
        # 安静房间的底噪(约 -66 dBFS)
        assert is_silent((np.random.randn(16000) * 0.0005).astype(np.float32))

    def test_speech_level_audio_is_not_silent(self):
        from services.stt_engine import is_silent

        t = np.arange(16000) / 16000
        assert not is_silent((0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
        # 一秒里只有 0.1 秒轻声说话,其余是停顿:峰值够高,不能判成静音
        quiet = np.zeros(16000, dtype=np.float32)
        quiet[:1600] = 0.05 * np.sin(2 * np.pi * 200 * t[:1600])
        assert not is_silent(quiet)

    def test_known_hallucinations_are_caught(self):
        from services.stt_engine import is_hallucination

        for text in ["The.", "Thank you so much for watching.", " 谢谢观看! ", "you"]:
            assert is_hallucination(text), text

    def test_real_sentences_are_kept(self):
        from services.stt_engine import is_hallucination

        for text in ["The meeting is at three.", "谢谢观看这个演示的各位同事", "明天见"]:
            assert not is_hallucination(text), text

    @pytest.mark.asyncio
    async def test_silent_audio_skips_the_model(self):
        from services.stt_server import STTEngine

        engine = STTEngine()
        engine._is_loaded = True
        engine._model_type = "whisper_turbo"

        class MustNotRun:
            def __call__(self, *a, **k):
                raise AssertionError("静音不该跑模型")

        engine._model = MustNotRun()
        result = await engine.transcribe(np.zeros(16000 * 3, dtype=np.int16).tobytes())
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_odd_length_audio_does_not_crash(self):
        from services.stt_server import STTEngine

        engine = STTEngine()
        engine._is_loaded = True
        engine._model_type = "whisper_turbo"
        engine._model = lambda *a, **k: {"text": "ok"}
        t = np.arange(16000) / 16000
        audio = (3000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes() + b"\x01"
        assert (await engine.transcribe(audio)).text == "ok"


class TestLoadingProgress:
    """加载期间 /health 报进度(F4)"""

    @pytest.mark.asyncio
    async def test_progress_reports_download_growth(self, monkeypatch):
        import asyncio

        from services.stt_server import STTEngine

        engine = STTEngine(default_model="whisper_tiny")
        sizes = iter([1000, 1000, 5000])
        monkeypatch.setattr(engine, "_cache_bytes", lambda: next(sizes))
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow_load():
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=5)

        monkeypatch.setattr(engine, "_load_model_sync", slow_load)
        assert engine.loading_progress() is None
        task = asyncio.create_task(engine.load())
        await asyncio.sleep(0.05)
        first = engine.loading_progress()  # 缓存还没长:在加载
        assert first["phase"] == "loading" and first["downloaded_bytes"] == 0
        second = engine.loading_progress()  # 长了 4000 字节:在下载
        assert second["phase"] == "downloading" and second["downloaded_bytes"] == 4000
        release.set()
        assert await task is True
        assert engine.loading_progress() is None


class TestKeepalive:
    """转写 / 后处理期间给客户端发心跳"""

    @pytest.mark.asyncio
    async def test_progress_is_sent_while_waiting(self, monkeypatch):
        import asyncio

        import services.stt_server as srv

        monkeypatch.setattr(srv, "KEEPALIVE_INTERVAL_S", 0.05)
        sent = []

        async def send(payload):
            sent.append(payload)
            return True

        async def slow():
            await asyncio.sleep(0.18)
            return "done"

        assert await srv._with_keepalive(slow(), "stt", send) == "done"
        assert len(sent) >= 2
        assert all(p["type"] == "progress" and p["stage"] == "stt" for p in sent)

    @pytest.mark.asyncio
    async def test_fast_work_sends_no_progress_and_errors_propagate(self):
        import services.stt_server as srv

        sent = []

        async def send(payload):
            sent.append(payload)
            return True

        async def fast():
            return 42

        async def boom():
            raise RuntimeError("model gone")

        assert await srv._with_keepalive(fast(), "stt", send) == 42
        assert sent == []
        with pytest.raises(RuntimeError, match="model gone"):
            await srv._with_keepalive(boom(), "stt", send)
