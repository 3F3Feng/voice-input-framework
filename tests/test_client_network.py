"""
客户端网络薄封装测试 — 验证 client/network.py 与 client/app.py 的接口契约(H5)

覆盖:SttClient.get_models / get_model_status / transcribe 与
     LlmClient.get_models / process / llm_url 默认值,
以及 app.py 中所有 self.stt.* / self.llm.* 调用均可解析。
"""
import asyncio
import sys
from pathlib import Path

import pytest

# Add project path
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))


class TestSttClientThinWrappers:
    """SttClient 薄封装方法"""

    def test_get_models_returns_list_of_dicts(self, monkeypatch):
        from client.network import SttClient

        client = SttClient("localhost", 6544)

        async def fake_fetch_models():
            client.available_models = ["qwen_asr", "whisper"]
            client.current_model = "qwen_asr"
            return True

        monkeypatch.setattr(client, "fetch_models", fake_fetch_models)
        models = asyncio.run(client.get_models())
        assert models == [
            {"name": "qwen_asr", "is_loaded": True, "is_current": True},
            {"name": "whisper", "is_loaded": False, "is_current": False},
        ]

    def test_get_models_empty_on_failure(self, monkeypatch):
        from client.network import SttClient

        client = SttClient("localhost", 6544)

        async def fake_fetch_models():
            return False

        monkeypatch.setattr(client, "fetch_models", fake_fetch_models)
        assert asyncio.run(client.get_models()) == []

    def test_get_model_status_returns_dict(self, monkeypatch):
        import httpx

        from client.network import SttClient

        client = SttClient("localhost", 6544)

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"is_loaded": True, "is_loading": False}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        status = asyncio.run(client.get_model_status("qwen_asr"))
        assert status == {"is_loaded": True, "is_loading": False}

    def test_transcribe_returns_text_dict(self, monkeypatch):
        from client.network import SttClient

        client = SttClient("localhost", 6544)

        async def fake_send_audio(audio_buffer):
            return "识别结果"

        monkeypatch.setattr(client, "send_audio", fake_send_audio)
        result = asyncio.run(client.transcribe(b"fake audio"))
        assert result == {"text": "识别结果"}

    def test_transcribe_none_on_failure(self, monkeypatch):
        from client.network import SttClient

        client = SttClient("localhost", 6544)

        async def fake_send_audio(audio_buffer):
            return None

        monkeypatch.setattr(client, "send_audio", fake_send_audio)
        result = asyncio.run(client.transcribe(b"fake audio"))
        assert result == {"text": ""}


class TestLlmClientThinWrappers:
    """LlmClient 薄封装方法"""

    def test_default_llm_url_uses_env(self, monkeypatch):
        from client.network import LlmClient

        monkeypatch.delenv("VIF_LLM_URL", raising=False)
        client = LlmClient("http://localhost:6544")
        assert client.llm_url == "http://127.0.0.1:6545"

    def test_get_models_returns_list_of_dicts(self, monkeypatch):
        from client.network import LlmClient

        client = LlmClient("http://localhost:6544")

        async def fake_fetch_models():
            client.available_models = ["Qwen3.5-4B-OptiQ", "Qwen3.5-2B-OptiQ"]
            client.current_model = "Qwen3.5-4B-OptiQ"
            return True

        monkeypatch.setattr(client, "fetch_models", fake_fetch_models)
        models = asyncio.run(client.get_models())
        assert models == [
            {"name": "Qwen3.5-4B-OptiQ", "is_current": True},
            {"name": "Qwen3.5-2B-OptiQ", "is_current": False},
        ]

    def test_process_posts_to_llm_url(self, monkeypatch):
        import httpx

        from client.network import LlmClient

        client = LlmClient("http://localhost:6544", llm_url="http://127.0.0.1:6545")
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"text": "处理后的文本"}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json):
                captured["url"] = url
                captured["json"] = json
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        result = asyncio.run(client.process("原始文本"))
        assert result == "处理后的文本"
        assert captured["url"] == "http://127.0.0.1:6545/process"
        assert captured["json"] == {"text": "原始文本"}

    def test_process_returns_original_on_failure(self, monkeypatch):
        import httpx

        from client.network import LlmClient

        client = LlmClient("http://localhost:6544", llm_url="http://127.0.0.1:6545")

        class FakeResponse:
            status_code = 503

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json):
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        result = asyncio.run(client.process("原始文本"))
        assert result == "原始文本"


class TestAppClientContract:
    """client/app.py 与 client/network.py 的接口契约(H5)"""

    def test_all_app_client_calls_resolvable(self):
        """app.py 中所有 self.stt.* / self.llm.* 调用在 network.py 中均存在"""
        import ast

        network_ast = ast.parse(Path(project_dir / "client" / "network.py").read_text())
        methods = {}
        for node in ast.walk(network_ast):
            if isinstance(node, ast.ClassDef) and node.name in ("SttClient", "LlmClient"):
                methods[node.name] = {
                    n.name for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }

        app_ast = ast.parse(Path(project_dir / "client" / "app.py").read_text())
        missing = []
        for node in ast.walk(app_ast):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                recv = node.func.value
                if isinstance(recv, ast.Attribute) and recv.attr in ("stt", "llm"):
                    cls = "SttClient" if recv.attr == "stt" else "LlmClient"
                    if node.func.attr not in methods[cls]:
                        missing.append(f"{recv.attr}.{node.func.attr}")

        assert missing == [], f"app.py 调用了 network.py 中不存在的方法: {sorted(set(missing))}"
