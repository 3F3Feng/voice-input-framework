"""
客户端网络薄封装测试 — 验证 client/network.py 与 client/app.py 的接口契约(H5)

覆盖:SttClient.get_models / get_model_status / transcribe 与
     LlmClient.get_models / process / llm_url 默认值,
以及 app.py 中所有 self.stt.* / self.llm.* 调用均可解析。
"""

import asyncio
import sys
from pathlib import Path

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
    """client/app.py 与各 client 模块的接口契约(H5)"""

    # app.py 中 self.<attr> → 真实类(模块路径,类名)
    ATTR_CLASS_MAP = {
        "audio": ("client.audio", "AudioRecorder"),
        "stt": ("client.network", "SttClient"),
        "llm": ("client.network", "LlmClient"),
        "config": ("client.config_manager", "ConfigManager"),
        "hotkey_manager": ("client.hotkey_manager", "HotkeyManager"),
        "window": ("client.ui", "MainWindow"),
        "tray": ("client.ui", "TrayMenu"),
    }

    @staticmethod
    def _class_methods(module: str, cls_name: str) -> set:
        import ast

        path = project_dir / (module.replace(".", "/") + ".py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                return {
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
        return set()

    def test_all_app_client_calls_resolvable(self):
        """app.py 中所有 self.<attr>.<method> 调用在对应类中均存在"""
        import ast

        app_ast = ast.parse(Path(project_dir / "client" / "app.py").read_text(encoding="utf-8"))
        missing = []
        for node in ast.walk(app_ast):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                recv = node.func.value
                if (
                    isinstance(recv, ast.Attribute)
                    and isinstance(recv.value, ast.Name)
                    and recv.value.id == "self"
                ):
                    if recv.attr not in self.ATTR_CLASS_MAP:
                        continue
                    module, cls = self.ATTR_CLASS_MAP[recv.attr]
                    methods = self._class_methods(module, cls)
                    if node.func.attr not in methods:
                        missing.append(f"{recv.attr}.{node.func.attr}")

        assert missing == [], f"app.py 调用了不存在的方法: {sorted(set(missing))}"

    def test_all_app_client_imports_resolvable(self):
        """app.py 中所有 from client.X import name 在对应模块中均存在"""
        import ast

        def module_symbols(module: str) -> set:
            path = project_dir / (module.replace(".", "/") + ".py")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            return {
                n.name
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }

        app_ast = ast.parse(Path(project_dir / "client" / "app.py").read_text(encoding="utf-8"))
        missing = []
        for node in ast.walk(app_ast):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("client.")
            ):
                symbols = module_symbols(node.module)
                for a in node.names:
                    if a.name not in symbols:
                        missing.append(f"{node.module}.{a.name}")

        assert missing == [], f"app.py 导入了不存在符号: {sorted(set(missing))}"

    def test_app_constructor_calls_match_signatures(self):
        """app.py 中 MainWindow/TrayMenu/IndicatorManager 构造参数与真实签名匹配"""
        import ast

        def ctor_signature(module: str, cls: str) -> tuple[list, list]:
            path = project_dir / (module.replace(".", "/") + ".py")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == cls:
                    for n in node.body:
                        if isinstance(n, ast.FunctionDef) and n.name == "__init__":
                            pos = [a.arg for a in n.args.args if a.arg != "self"]
                            return pos, [a.arg for a in n.args.kwonlyargs]
            return [], []

        # 类名 → 定义模块
        CLASS_MODULE = {
            "MainWindow": "client.ui",
            "TrayMenu": "client.ui",
            "IndicatorManager": "client.ui",
        }

        app_ast = ast.parse(Path(project_dir / "client" / "app.py").read_text(encoding="utf-8"))
        problems = []
        for node in ast.walk(app_ast):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in CLASS_MODULE
            ):
                cls = node.func.id
                pos_params, kwonly = ctor_signature(CLASS_MODULE[cls], cls)
                if len(node.args) > len(pos_params):
                    problems.append(
                        f"{cls}: {len(node.args)} 个位置参数,签名最多 {len(pos_params)}"
                    )
                for k in node.keywords:
                    if k.arg not in (pos_params + kwonly):
                        problems.append(f"{cls}: 未知关键字参数 {k.arg!r}")

        assert problems == [], f"构造函数调用与签名不匹配: {problems}"
