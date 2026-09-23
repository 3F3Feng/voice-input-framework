"""服务端提示按 Accept-Language 回中文 / 英文(F22)"""

import httpx
import pytest
from fastapi.testclient import TestClient

from shared import auth, i18n
from shared.i18n import bi

EN = {"Accept-Language": "en"}


class TestHeaderParsing:
    @pytest.mark.parametrize(
        "value, want",
        [
            (None, "zh"),  # 老客户端、curl:和以前一样回中文
            ("", "zh"),
            ("en", "en"),
            ("zh", "zh"),
            ("EN-us", "en"),
            ("en-US,en;q=0.9", "en"),
            ("zh-CN,zh;q=0.9,en;q=0.8", "zh"),
            ("zh-Hant-TW", "zh"),
            ("en;q=0.5, zh;q=0.8", "zh"),  # 按 q 值,不按先后
            ("fr-FR, en;q=0.7", "en"),  # 不认识的跳过
            ("fr, de", "zh"),  # 全不认识:回中文
            ("en;q=0, zh", "zh"),  # q=0 表示不要
            ("*", "zh"),
            ("english", "zh"),  # 不是 en 开头的语言标签
            ("en;q=abc", "zh"),
        ],
    )
    def test_lang_from_header(self, value, want):
        assert i18n.lang_from_header(value) == want

    def test_t(self):
        assert i18n.t("en", "中文", "English") == "English"
        assert i18n.t("zh", "中文", "English") == "中文"

    def test_bilingual_is_the_chinese_string(self):
        """存起来的提示当普通 str 用就是原来那句中文,老代码和老测试不受影响。"""
        msg = bi("加载失败", "Load failed")
        assert msg == "加载失败"
        assert f"原因:{msg}" == "原因:加载失败"
        assert i18n.localize("en", msg) == "Load failed"
        assert type(i18n.localize("zh", msg)) is str
        assert i18n.localize("en", "raw error") == "raw error"
        assert i18n.localize("en", None) is None

    def test_exception_messages(self):
        e = RuntimeError(bi("没装 X", "X is not installed"))
        assert str(e) == "没装 X"
        assert i18n.exc_text("en", e) == "X is not installed"
        assert i18n.exc_text("zh", e) == "没装 X"
        both = i18n.exc_bilingual(e)
        assert both == "RuntimeError: 没装 X"
        assert i18n.localize("en", both) == "RuntimeError: X is not installed"
        # 第三方库的原始报错原样给
        assert i18n.exc_text("en", ValueError("boom")) == "boom"
        assert i18n.exc_bilingual(ValueError("boom")) == "ValueError: boom"


@pytest.fixture
def stt():
    import services.stt_server as srv

    return srv


class TestSTTServer:
    def test_model_catalog_in_both_languages(self, stt):
        client = TestClient(stt.app)
        zh = {m["name"]: m for m in client.get("/models").json()}
        en = {m["name"]: m for m in client.get("/models", headers=EN).json()}
        assert zh["whisper_small"]["description"] == "Whisper Small (transformers, 速度与精度折中)"
        assert en["whisper_small"]["description"] == (
            "Whisper Small (transformers, balance of speed and accuracy)"
        )
        # 本来就是英文的说明两种语言一样
        assert en["whisper_cpp_base"]["description"] == zh["whisper_cpp_base"]["description"]
        for name, m in en.items():
            reason = m["unavailable_reason"]
            assert reason is None or not any("一" <= ch <= "鿿" for ch in reason), name

    def test_bad_request_detail(self, stt, monkeypatch):
        monkeypatch.setattr(stt, "save_state", lambda state: pytest.fail("不该持久化"))
        client = TestClient(stt.app)
        r = client.put("/vocabulary", json={"entries": "x"})
        assert r.status_code == 400
        assert r.json()["detail"] == "entries 必须是字符串列表"
        r = client.put("/vocabulary", json={"entries": "x"}, headers=EN)
        assert r.json()["detail"] == "entries must be a list of strings"

    def test_llm_unsupported_reason(self, stt, monkeypatch):
        monkeypatch.setattr(stt, "LLM_SUPPORTED", False)
        monkeypatch.setattr(stt, "LLM_UNSUPPORTED_REASON", bi("不支持", "Not supported"))
        client = TestClient(stt.app)
        assert client.get("/llm/enabled").json()["reason"] == "不支持"
        assert client.get("/llm/enabled", headers=EN).json()["reason"] == "Not supported"
        r = client.put("/llm/enabled", json={"enabled": True}, headers=EN)
        assert r.status_code == 409
        assert r.json()["error_message"] == "Not supported"

    def test_health_hardware_placeholder(self, stt, monkeypatch):
        from services.stt_engine import STTEngine

        monkeypatch.setattr(stt, "engine", STTEngine())
        client = TestClient(stt.app)
        assert client.get("/health").json()["hardware"] == {"status": "模型尚未加载"}
        assert client.get("/health", headers=EN).json()["hardware"] == {
            "status": "Model not loaded yet"
        }

    def test_transcribe_error_is_localized(self, stt, monkeypatch):
        """转写报的「模型加载失败」按请求的语言说"""
        from services.stt_engine import STTEngine

        engine = STTEngine()

        async def failing_load():
            engine._load_error = i18n.exc_bilingual(RuntimeError(bi("缺库", "missing lib")))
            return False

        monkeypatch.setattr(engine, "load", failing_load)
        monkeypatch.setattr(stt, "engine", engine)
        client = TestClient(stt.app)
        audio = {"file": ("a.pcm", b"\x10\x00" * 1600)}
        zh = client.post("/transcribe", files=audio).json()["detail"]
        en = client.post("/transcribe", files=audio, headers=EN).json()["detail"]
        assert "加载失败" in zh and "缺库" in zh
        assert en.startswith("Failed to load STT model") and "missing lib" in en

    def test_unsupported_upload_is_localized(self, stt):
        client = TestClient(stt.app)
        mp3 = {"file": ("a.mp3", b"ID3" + b"\x00" * 64)}
        assert "暂不支持 MP3" in client.post("/transcribe", files=mp3).json()["detail"]
        en = client.post("/transcribe", files=mp3, headers=EN).json()["detail"]
        assert en.startswith("MP3 is not supported")

    @pytest.mark.asyncio
    async def test_llm_call_forwards_language(self, stt, monkeypatch):
        """转发给 LLM 服务时带上客户端的语言;连不上时的提示也跟着换"""
        seen = []
        real_init = httpx.AsyncClient.__init__

        def spy_init(self, *a, **k):
            seen.append(dict(k.get("headers") or {}))
            real_init(self, *a, **k)

        async def refuse(self, *a, **k):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx.AsyncClient, "__init__", spy_init)
        monkeypatch.setattr(httpx.AsyncClient, "post", refuse)
        _, _, err = await stt.call_llm_server("原文", lang="en")
        assert err == "Cannot reach the LLM service (it may not be running)"
        assert seen[-1]["Accept-Language"] == "en"
        _, _, err = await stt.call_llm_server("原文")
        assert err == "连不上 LLM 服务(可能没有启动)"
        assert seen[-1]["Accept-Language"] == "zh"

    def test_ws_timeout_message(self, stt, monkeypatch):
        """WS 的错误消息按握手时的语言说"""
        import base64
        import json

        from services.stt_engine import STTEngine

        engine = STTEngine()

        async def slow(*a, **k):
            raise TimeoutError

        monkeypatch.setattr(engine, "transcribe", slow)
        monkeypatch.setattr(stt, "engine", engine)
        for headers, want in [({}, "转写超时"), (EN, "Transcription timed out")]:
            with TestClient(stt.app).websocket_connect("/ws/stream", headers=headers) as ws:
                assert json.loads(ws.receive_text())["type"] == "ready"
                audio = base64.b64encode(b"\x01\x00" * 1600).decode()
                ws.send_text(json.dumps({"type": "audio", "data": audio}))
                ws.send_text(json.dumps({"type": "end"}))
                msg = json.loads(ws.receive_text())
            assert msg["type"] == "error" and msg["error_message"] == want


class TestUnauthorized:
    def test_401_message_is_localized(self, stt, monkeypatch):
        import services.llm_server as llm

        monkeypatch.setenv("VIF_API_TOKEN", "s3cret")
        for app in (stt.app, llm.app):
            client = TestClient(app)
            r = client.get("/models")
            assert r.status_code == 401
            assert r.json()["error_message"] == auth.UNAUTHORIZED_MESSAGE
            r = client.get("/models", headers={"Accept-Language": "en-US,en;q=0.9"})
            assert r.status_code == 401
            assert r.json()["error_message"] == auth.UNAUTHORIZED_MESSAGE_EN
            assert "token" in r.json()["error_message"]


class TestLLMServer:
    @pytest.fixture(autouse=True)
    def _no_backend_override(self, monkeypatch):
        monkeypatch.delenv("VIF_LLM_BACKEND", raising=False)

    @pytest.mark.asyncio
    async def test_load_error_in_both_languages(self, monkeypatch):
        import services.llm_server as srv

        engine = srv.LLMEngine(backend=srv.MLXBackend())
        assert await engine.load("No-Such-Model") is False
        monkeypatch.setattr(srv, "engine", engine)
        client = TestClient(srv.app)
        assert client.get("/health").json()["error"] == "未知的 LLM 模型:No-Such-Model"
        assert client.get("/health", headers=EN).json()["error"] == (
            "Unknown LLM model: No-Such-Model"
        )

    def test_backend_unavailable_reason_survives_the_exception(self):
        """后端不可用的原因经 RuntimeError 抛出、存进 load_error 之后,英文那份还在"""
        import services.llm_server as srv
        from shared import llm_backend

        _, reason = llm_backend.choose_backend(False, has=lambda name: False, platform="linux")
        engine = srv.LLMEngine(backend=srv.LlamaCppBackend(unavailable=reason))
        with pytest.raises(RuntimeError) as exc:
            engine._load_sync("x/y/z.gguf")
        stored = i18n.exc_bilingual(exc.value)
        assert stored.startswith("RuntimeError: 这台机器上的 LLM 后处理要用 llama.cpp")
        assert i18n.localize("en", stored).startswith(
            "RuntimeError: LLM post-processing on this machine needs llama.cpp"
        )

    def test_rejected_output_reason(self, monkeypatch):
        import services.llm_server as srv

        engine = srv.LLMEngine(backend=srv.MLXBackend())
        engine._model, engine._tokenizer, engine._is_loaded = object(), object(), True
        monkeypatch.setattr(srv, "load_prompt", lambda: "prompt")
        monkeypatch.setattr(engine.backend, "generate", lambda *a: ("", False))
        assert engine.process("嗯那个明天开会").error == "LLM 返回了空结果"
        assert engine.process("嗯那个明天开会", lang="en").error == "LLM returned an empty result"

        monkeypatch.setattr(srv, "engine", engine)
        r = TestClient(srv.app).post("/process", json={"text": "嗯那个明天开会"}, headers=EN)
        assert r.status_code == 200
        assert r.json()["success"] is False
        assert r.json()["error"] == "LLM returned an empty result"
