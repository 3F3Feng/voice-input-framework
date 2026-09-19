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


class TestHotkeyPermissionCheck:
    """HotkeyManager 权限自检逻辑(macOS 辅助功能权限缺失检测)"""

    def _make_manager(self):
        from client.hotkey_manager import HotkeyManager

        m = HotkeyManager()
        m._listener_started_at = 1000.0  # mock 启动时间
        return m

    def _require_pynput(self):
        pytest.importorskip("pynput")

    def test_no_events_within_quiet_period_returns_false(self, monkeypatch):
        """窗口内零事件 → 判定权限异常(返回 False)"""
        self._require_pynput()
        m = self._make_manager()
        m.event_count = 0
        monkeypatch.setattr("time.time", lambda: 1006.0)  # 启动后 6s(>5s 窗口)
        assert m.check_listener_activity(quiet_period=5.0) is False

    def test_events_received_returns_true(self):
        """收到过事件 → 监听正常(返回 True)"""
        self._require_pynput()
        m = self._make_manager()
        m.event_count = 3
        assert m.check_listener_activity(quiet_period=5.0) is True

    def test_within_quiet_period_no_events_is_ok(self, monkeypatch):
        """窗口内无事件但未超时 → 暂不判定异常(返回 True)"""
        self._require_pynput()
        m = self._make_manager()
        m.event_count = 0
        monkeypatch.setattr("time.time", lambda: 1002.0)  # 启动后 2s(<5s 窗口)
        assert m.check_listener_activity(quiet_period=5.0) is True

    def test_listener_never_started_returns_false(self):
        """监听器从未启动 → 判定异常(返回 False)"""
        self._require_pynput()
        from client.hotkey_manager import HotkeyManager

        m = HotkeyManager()
        m._listener_started_at = None
        assert m.check_listener_activity() is False

    def test_event_count_increments_on_press(self):
        """_on_key_press 递增 event_count(自检数据源)"""
        self._require_pynput()
        from client.hotkey_manager import HotkeyManager

        m = HotkeyManager()
        before = m.event_count
        # 模拟按键事件(用无效 key 也可,计数在 try 前递增)
        m._on_key_press(None)
        assert m.event_count == before + 1


class TestMicPermissionCheck:
    """AudioRecorder 麦克风权限检测"""

    def test_non_macos_returns_none(self, monkeypatch):
        """非 macOS 平台无法判定 → 返回 None"""
        from client.audio import AudioRecorder

        monkeypatch.setattr("sys.platform", "linux")
        assert AudioRecorder.check_mic_permission() is None


class TestResolveInputDevice:
    """AudioRecorder._resolve_input_device 设备解析逻辑"""

    class _FakeDev:
        def __init__(self, name, inn, out):
            self.name = name
            self.max_input_channels = inn
            self.max_output_channels = out

        def get(self, k, default=None):
            return getattr(self, k, default)

    class _FakeDefault:
        def __init__(self, device):
            self.device = device

    def _make_sd(self, default_input):
        devs = [
            self._FakeDev("Mic", 1, 0),
            self._FakeDev("Speakers", 0, 2),
            self._FakeDev("Teams", 2, 2),
        ]
        default = self._FakeDefault((default_input, 0))

        class FakeSD:
            def query_devices(self, d=None):
                return devs if d is None else devs[d]

        sd = FakeSD()
        sd.default = default
        return sd

    def test_skips_default_with_no_input_channels(self):
        """默认输入指向 0 输入通道(Speakers)→ 跳过,选第一个有输入的(Mic=0)"""
        from client.audio import AudioRecorder

        sd = self._make_sd(default_input=1)  # 默认输入=Speakers(0 in)
        assert AudioRecorder._resolve_input_device(sd) == 0

    def test_uses_default_when_it_has_input(self):
        """默认输入有输入通道 → 直接用默认"""
        from client.audio import AudioRecorder

        sd = self._make_sd(default_input=2)  # 默认输入=Teams(2 in)
        assert AudioRecorder._resolve_input_device(sd) == 2

    def test_default_none_picks_first_input(self):
        """无默认输入 → 选第一个有输入的(Mic=0)"""
        from client.audio import AudioRecorder

        sd = self._make_sd(default_input=None)
        assert AudioRecorder._resolve_input_device(sd) == 0


class TestIndicatorPosition:
    """浮标位置计算:统一偏移 + 屏幕边缘翻转"""

    SS = (1920, 1080)

    def test_normal_offset_top_right(self):
        """常规位置:右上方 +10,-50"""
        from client.floating_indicator import calculate_indicator_position as f

        assert f((960, 540), (100, 40), self.SS) == (970, 490)

    def test_flip_left_at_right_edge(self):
        """靠右边缘 → 翻到左方"""
        from client.floating_indicator import calculate_indicator_position as f

        assert f((1900, 540), (100, 40), self.SS) == (1790, 490)

    def test_flip_down_at_top_edge(self):
        """靠上边缘 → 翻到下方(窗口左上角贴基准点右下方)"""
        from client.floating_indicator import calculate_indicator_position as f

        assert f((960, 20), (100, 40), self.SS) == (970, 30)

    def test_flip_both_at_corner(self):
        """右上角 → 左+下 双翻转(窗口右上角贴基准点左下方)"""
        from client.floating_indicator import calculate_indicator_position as f

        assert f((1900, 20), (100, 40), self.SS) == (1790, 30)

    def test_no_screen_size_no_flip(self):
        """屏幕尺寸充足时右上方不翻转(显式传屏幕,不依赖环境检测)"""
        from client.floating_indicator import calculate_indicator_position as f

        # 2560x1440 下 1900 右缘充足,不翻转(结果与运行环境无关)
        assert f((1900, 540), (100, 40), (2560, 1440)) == (1910, 490)

    def test_none_pos_default(self):
        """无基准点 → 默认位置"""
        from client.floating_indicator import calculate_indicator_position as f

        assert f(None, (100, 40), self.SS) == (1200, 100)


class TestUIEventContract:
    """UI 布局 key 与 app.py 事件处理一致性(防拆分脱节)"""

    def test_interactive_keys_have_handlers(self):
        """UI 中所有可交互 key(Button/Checkbox/带事件的控件)在 app.py 有处理"""
        import re

        ui_src = Path(project_dir / "client" / "ui.py").read_text(encoding="utf-8")
        app_src = Path(project_dir / "client" / "app.py").read_text(encoding="utf-8")

        # 纯显示/输入控件(值由其他事件读取,无需独立分支)
        value_only = {
            "-HOST-",
            "-PORT-",
            "-HOTKEY-",
            "-LLM-PROMPT-",
            # 纯显示元素(仅被 update,不发事件)
            "-STATUS-",
            "-CONN-STATUS-",
            "-ERROR-",
            "-LOG-",
            "-RESULT-",
            "-MODEL-STATUS-",
            "-LLM-MODEL-STATUS-",
            "-PROMPT-STATUS-",
        }
        app_events = set(re.findall(r'event == "(-[A-Z0-9-]+)"', app_src))
        ui_keys = set(re.findall(r'key="(-[A-Z0-9-]+)"', ui_src))
        interactive = ui_keys - value_only

        missing = sorted(k for k in interactive if k not in app_events)
        assert missing == [], f"UI key 无对应事件处理: {missing}"


class TestTrayCallbackContract:
    """托盘菜单回调与 app.py 接线一致性(防托盘菜单点了没反应)"""

    def test_tray_callbacks_match_setup(self):
        """create_menu 引用的回调 key 都应在 app.py 的 tray.setup 中提供"""
        import re

        tray_src = Path(project_dir / "client" / "tray_manager.py").read_text(encoding="utf-8")
        app_src = Path(project_dir / "client" / "app.py").read_text(encoding="utf-8")

        menu_cb = set(re.findall(r'_call_callback\("([a-z_]+)"\)', tray_src))
        setup_cb = set(
            re.findall(
                r'"(show_window|hide_window|start_recording|stop_recording|refresh_models|check_update|toggle_auto_start|quit)"\s*:',
                app_src,
            )
        )

        missing = sorted(menu_cb - setup_cb)
        assert missing == [], f"托盘菜单回调未在 app.py setup 中提供: {missing}"


class TestMacHidKeyMapping:
    """CGEventTap 的 macOS HID 键码 → 键名/字符映射(防 Windows VK 错配回归)

    macOS HID 键码与 Windows VK 完全不同:字母 A=0x00(不是 0x41)、
    数字 1=0x12(不是 0x31)。映射错误会导致带字母/数字主键的快捷键
    (如 alt+v)永远不触发。
    """

    @staticmethod
    def _listener():
        try:
            import pynput  # noqa: F401
        except ImportError:
            # 无 pynput 环境:注入最小 stub(hotkey_manager 顶层只用
            # keyboard 名字;HID 映射逻辑本身不依赖 pynput)
            import sys
            import types

            kb = types.ModuleType("pynput.keyboard")

            class _KeyCode:
                def __init__(self, vk=None, char=None):
                    self.vk = vk
                    self.char = char

                @classmethod
                def from_vk(cls, vk):
                    return cls(vk=vk)

            kb.KeyCode = _KeyCode
            kb.Key = type(
                "Key",
                (),
                {
                    "shift": _KeyCode(),
                    "ctrl": _KeyCode(),
                    "alt": _KeyCode(),
                    "cmd": _KeyCode(),
                },
            )
            pynput_mod = types.ModuleType("pynput")
            pynput_mod.keyboard = kb
            sys.modules.setdefault("pynput", pynput_mod)
            sys.modules.setdefault("pynput.keyboard", kb)

        from client.hotkey_manager import _MacOSEventTapListener

        return _MacOSEventTapListener(on_press=lambda k: None, on_release=lambda k: None)

    def test_letter_keys_use_macos_hid_keycodes(self):
        lis = self._listener()
        key = lis._key_from_vk(0x09)  # V (macOS HID)
        assert key.name == "v" and key.char == "v"
        key = lis._key_from_vk(0x00)  # A
        assert key.name == "a" and key.char == "a"
        key = lis._key_from_vk(0x2D)  # N
        assert key.name == "n" and key.char == "n"

    def test_digit_keys_use_macos_hid_keycodes(self):
        lis = self._listener()
        key = lis._key_from_vk(0x12)  # 1
        assert key.name == "1" and key.char == "1"
        key = lis._key_from_vk(0x1D)  # 0
        assert key.name == "0" and key.char == "0"

    def test_modifier_and_function_keys(self):
        lis = self._listener()
        assert lis._key_from_vk(0x3B).name == "ctrl_l"
        assert lis._key_from_vk(0x37).name == "cmd_l"
        assert lis._key_from_vk(0x31).name == "space"
        assert lis._key_from_vk(0x69).name == "f13"  # 预设里有 f13

    def test_hid_key_matches_main_key(self):
        """macOS HID 键码的 V 键(_TapKey 带 char)能匹配主键 'v'"""
        from client.hotkey_manager import HotkeyManager, _TapKey

        hm = HotkeyManager()
        hm.set_hotkey("alt+v")
        hm.pressed_keys.add(_TapKey("v", 0x09, char="v"))
        assert hm._is_main_key_pressed() is True
        # 老实现(Windows VK 误配)会产生 name="vk_9" → 匹配失败,此测试拦截

    def test_hid_key_vk_not_windows_vk(self):
        """字母键 vk 不应落入 Windows VK 字母范围(0x41-0x5A)"""
        lis = self._listener()
        key = lis._key_from_vk(0x09)  # macOS V
        assert not (0x41 <= key.vk <= 0x5A)


class TestAppAsyncEventContract:
    """app.py 后台线程不直接碰 Tk + 事件投递齐全(防卡顿/指示器卡死回归)

    识别/LLM 在 asyncio 线程运行;直接调 window.set_status / update_result /
    indicators.hide_processing 会触发 tkinter "main thread is not in main
    loop" 错误(被吞后处理中指示器卡住),必须经 write_event_value 投递。
    """

    @staticmethod
    def _process_audio_node():
        import ast

        src = Path(project_dir / "client" / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if (
                isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                and n.name == "_process_audio"
            ):
                return n
        raise AssertionError("app.py 缺少 _process_audio")

    def test_process_audio_has_no_direct_tk_calls(self):
        import ast

        node = self._process_audio_node()
        attrs = {
            n.func.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        forbidden = {"set_status", "update_result", "hide_processing", "update_model_status"}
        assert not (forbidden & attrs), f"_process_audio 直接调 Tk: {forbidden & attrs}"

    def test_async_events_handled_in_main_loop(self):
        app_src = Path(project_dir / "client" / "app.py").read_text(encoding="utf-8")
        for ev in ("-STATUS-", "-RESULT-READY-", "-AUTO-INPUT-"):
            assert f'event == "{ev}"' in app_src, f"主循环缺少 {ev} 事件处理"

    def test_auto_input_async_exists(self):
        import ast

        src = Path(project_dir / "client" / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {
            n.name for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
        }
        assert "_auto_input_async" in names
        assert "_auto_input_text" in names  # 被 to_thread 包裹的同步实现仍在
