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

        engine = STTEngine()
        import platform

        expected = (
            "qwen_asr_mlx_native_small"
            if (platform.machine() == "arm64" and platform.system() == "Darwin")
            else "whisper_turbo"
        )
        assert engine.default_model == expected
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
        fake_mlx_whisper.transcribe = lambda *a, **k: {"text": "hello", "language": "en"}
        monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx_whisper)

        audio = np.zeros(16000, dtype=np.int16).tobytes()
        result = await engine.transcribe(audio)
        assert isinstance(result, TranscriptionResult)
        assert result.text == "hello"

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

        audio = np.zeros(16000, dtype=np.int16).tobytes()
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

        audio = np.zeros(16000, dtype=np.int16).tobytes()
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

        audio = np.zeros(16000, dtype=np.int16).tobytes()
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
