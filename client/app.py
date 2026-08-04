#!/usr/bin/env python3
"""
Voice Input Framework - 客户端应用控制器

协调 AudioRecorder + SttClient/LlmClient + MainWindow/TrayMenu。
处理热键、录音、网络通信、UI 事件循环。
"""

import asyncio
import logging
import os
import sys
import threading
import time

import PySimpleGUI as sg

from client.audio import AudioRecorder
from client.config_manager import ConfigManager
from client.hotkey_manager import HotkeyManager, HotkeyPresets
from client.network import LlmClient, SttClient
from client.ui import IndicatorManager, MainWindow, TrayMenu
from client.tray_manager import TrayIconManager
from client.auto_start import AutoStartManager

logger = logging.getLogger(__name__)

# 自动输入:macOS 用剪贴板 + Cmd+V(见 _auto_input_text)


class VoiceInputApp:
    """语音输入应用控制器"""

    def __init__(self, server_host: str | None = None, server_port: int | None = None):
        self.config = ConfigManager()
        self.server_host = server_host or self.config.server_host
        self.server_port = server_port or self.config.server_port

        # 服务客户端
        self.stt = SttClient(self.server_host, self.server_port)
        # LLM:配置管理走 STT 服务(6544)的 /llm/* 转发层;process 直连 LLM 服务(默认 6545)
        llm_host = os.environ.get("VIF_LLM_HOST", "127.0.0.1")
        llm_port = os.environ.get("VIF_LLM_PORT", "6545")
        self.llm = LlmClient(
            f"http://{self.server_host}:{self.server_port}",
            llm_url=f"http://{llm_host}:{llm_port}",
        )

        # 音频
        self.audio = AudioRecorder()
        self.selected_mic: int | None = None
        self._audio_devices: dict = {}  # 设备名 → 设备 id(macOS UI 用名称选择)
        self._frontmost_app: str | None = None  # 录音时的前台应用(粘贴前激活)

        # UI
        self.window: MainWindow | None = None
        self.tray: TrayMenu | None = None

        # 状态
        self.is_running = False
        self._hotkey_pressed = False

        # 异步
        self.async_loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: threading.Thread | None = None

        # 热键
        self.hotkey_manager = HotkeyManager()

        # UI 回调:音频电平(直接透传 get_audio_level 的 (db, db) 二元组)
        self._get_audio_level = self.audio.get_audio_level

    def _make_audio_level_callback(self):
        """音量回调包装（兼容悬浮指示器接口）"""
        return lambda: self._get_audio_level()

    # ── 服务器连接 ──

    async def _connect(self):
        await self.stt.connect()
        if self.stt.is_connected:
            models = await self.stt.get_models()
            if self.window:
                self.window.set_status(f"已连接 {self.server_host}:{self.server_port}", "green")
                self.window.update_model_list(
                    [m["name"] for m in models],
                    self.stt.current_model or (models[0]["name"] if models else ""),
                )
        else:
            if self.window:
                self.window.set_status("连接失败", "red")

    # ── 模型管理 ──

    async def _fetch_models(self):
        if not self.stt.is_connected:
            await self._connect()
        models = await self.stt.get_models()
        if self.window:
            names = [m["name"] for m in models]
            self.window.update_model_list(names, self.stt.current_model or "")
            self.window.set_status(f"已加载 {len(models)} 个模型", "green")

    async def _switch_model(self, name: str):
        if self.window:
            self.window.set_status(f"切换模型: {name}...", "yellow")
        ok = await self.stt.switch_model(name)
        if self.window:
            self.window.update_model_status(
                f"切换请求已接受: {name}" if ok else f"切换失败: {name}", "yellow"
            )
        # 轮询加载状态
        await self._poll_model_loading(name)

    async def _poll_model_loading(self, name: str):
        for _ in range(30):
            status = await self.stt.get_model_status(name)
            if status.get("is_loaded"):
                if self.window:
                    self.window.update_model_status(f"模型 {name} 已加载 ✅", "green")
                return
            if not status.get("is_loading"):
                break
            await asyncio.sleep(1)
        if self.window:
            self.window.update_model_status(f"模型 {name} 加载超时", "red")

    # ── LLM 管理 ──

    async def _fetch_llm_models(self):
        models = await self.llm.get_models()
        if self.window:
            names = [m["name"] for m in models]
            current = self.llm.current_model or (models[0]["name"] if models else "")
            self.window.update_llm_model_list(names, current)

    async def _switch_llm_model(self, name: str):
        if self.window:
            self.window.update_llm_model_status(f"切换模型: {name}...", "cyan")
        ok = await self.llm.switch_model(name)
        if self.window:
            self.window.update_llm_model_status(
                f"切换成功: {name}" if ok else f"切换失败: {name}", "cyan"
            )

    async def _load_prompt(self):
        prompt = await self.llm.load_prompt()
        if self.window:
            self.window.update_prompt(prompt or "")

    async def _save_prompt(self, text: str):
        await self.llm.save_prompt(text)
        if self.window:
            self.window.update_prompt_status("已保存 ✅", "green")

    # ── 录音和转录 ──

    async def _start_recording(self):
        # 记录当前前台应用(macOS:粘贴前需先激活它,否则 Cmd+V 进的是客户端自己)
        self._frontmost_app = self._get_frontmost_app()
        if self.selected_mic is not None:
            self.audio.selected_device = self.selected_mic
        try:
            self.audio.start_recording()
        except Exception as e:
            # 录音启动失败(常见:macOS 麦克风权限未授权/设备不可用)→ 明确提示而非静默
            logger.error(f"启动录音失败: {e}")
            msg = (
                "❌ 无法开始录音:麦克风可能未授权或不可用。\n"
                "请到 系统设置 → 隐私与安全性 → 麦克风,\n"
                "为当前终端/应用开启权限后重启。"
            )
            if self.window:
                self.window.log(msg)
                self.window.set_status("录音失败", "red")
            return
        self._hotkey_pressed = True
        if self.window:
            self.window.write_event_value("-REC-STARTED-", "")

    async def _stop_recording(self):
        self._hotkey_pressed = False
        if self.window:
            self.window.write_event_value("-REC-STOPPED-", "")

    def _on_recording_started(self):
        if getattr(self, "indicators", None):
            self.indicators.show_recording()

    def _on_recording_stopped(self):
        if getattr(self, "indicators", None):
            self.indicators.hide_recording()
            self.indicators.show_processing()

    async def _process_audio(self):
        """处理已录制的音频"""
        self.audio.stop_recording()
        audio_data = self.audio.get_full_audio()
        if getattr(self, "indicators", None):
            self.indicators.hide_processing()
        if not audio_data or len(audio_data) < 320:
            return

        # 通过 LLM 后处理或直接返回
        llm_enabled = self.config.llm_enabled
        self.window.set_status("正在识别...", "yellow")
        result = await self.stt.transcribe(audio_data, language="auto")
        text = result.get("text", "")

        if text and llm_enabled:
            self.window.set_status("正在 LLM 后处理...", "cyan")
            text = await self.llm.process(text)

        # 显示和输入
        if self.window:
            self.window.update_result(text)
            self.window.write_event_value("-AUTO-INPUT-", text)
            self.window.set_status("就绪", "green")

    # ── 文本输入 ──

    @staticmethod
    def _get_frontmost_app() -> str | None:
        """获取当前前台应用名称(macOS;供粘贴前激活)"""
        if sys.platform != "darwin":
            return None
        try:
            import subprocess

            out = subprocess.check_output(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                ],
                timeout=3,
            )
            return out.decode("utf-8", errors="ignore").strip()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _activate_frontmost_app(name: str | None) -> bool:
        """激活指定应用(macOS);失败返回 False"""
        if not name or sys.platform != "darwin":
            return False
        try:
            import subprocess

            subprocess.run(
                ["osascript", "-e", f'tell application "{name}" to activate'],
                check=True,
                timeout=5,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _copy_to_clipboard(text: str) -> bool:
        """将文本复制到系统剪贴板"""
        if not text:
            return False
        try:
            import pyperclip

            pyperclip.copy(text)
            return True
        except Exception:  # noqa: BLE001
            pass
        try:
            import subprocess

            cmd = ["pbcopy"] if sys.platform == "darwin" else ["xclip", "-selection", "clipboard"]
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _auto_input_text(self, text: str):
        if not text:
            return
        try:
            if sys.platform == "darwin":
                # macOS:剪贴板 + Cmd+V 粘贴(对中文/特殊字符可靠)。
                # 先激活录音时的前台应用,否则 Cmd+V 进的是客户端自己的窗口。
                self._activate_frontmost_app(getattr(self, "_frontmost_app", None))
                try:
                    import subprocess

                    self._copy_to_clipboard(text)
                    subprocess.run(
                        [
                            "osascript",
                            "-e",
                            'tell application "System Events" to keystroke "v" using command down',
                        ],
                        check=True,
                        timeout=5,
                    )
                    return
                except Exception:  # noqa: BLE001
                    pass
                try:
                    import pyautogui

                    self._copy_to_clipboard(text)
                    pyautogui.hotkey("cmd", "v")
                    return
                except Exception:  # noqa: BLE001
                    pass
                import subprocess

                process = subprocess.Popen(
                    ["osascript", "-e", f'tell application "System Events" to keystroke "{text}"'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                process.wait(timeout=5)
            else:
                import pyautogui

                pyautogui.typewrite(text, interval=0.01)
        except Exception as e:
            logger.error(f"自动输入失败: {e}")

    # ── 异步线程 ──

    def _run_async_loop(self):
        self.async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.async_loop)
        self.async_loop.run_forever()

    # ── 主循环 ──

    def run(self):
        """主事件循环"""
        devices = AudioRecorder.get_devices()
        self._audio_devices = devices  # {id: name},供 -MICROPHONE- 反查
        self.window = MainWindow(
            config_manager=self.config,
            audio_devices=devices,
            server_host=self.server_host,
            server_port=self.server_port,
        )
        self.indicators = IndicatorManager(self._make_audio_level_callback())
        _window = self.window.create_window(start_minimized=self.config.start_minimized)
        self.is_running = True

        # 启动异步线程
        self.loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.loop_thread.start()
        time.sleep(0.1)

        # 设置托盘:仅 Windows/Linux(macOS 上系统菜单栏对 Python 进程的
        # NSStatusItem 支持受限,已实测 pystray/原生/rumps/Swift 均无法显示,
        # 故 macOS 用"最小化到 Dock"替代)
        if _window and sys.platform != "darwin":
            self.tray = TrayMenu(TrayIconManager(), AutoStartManager())
            self.tray.setup(
                {
                    # 通过主循环事件驱动(与 UI 按钮共用处理路径)
                    "show_window": lambda: _window.write_event_value("-SHOW-WINDOW-", None),
                    "hide_window": lambda: _window.write_event_value("-MINIMIZE-TRAY-", None),
                    "start_recording": lambda: self._async_task(self._start_recording()),
                    "stop_recording": lambda: self._async_task(self._stop_recording()),
                    "refresh_models": lambda: self._async_task(self._fetch_models()),
                    "check_update": lambda: self._async_task(self._check_update()),
                    "toggle_auto_start": self._toggle_auto_start,
                    "quit": lambda: _window.write_event_value("-EXIT-", None),
                }
            )
            self.tray.start()

        # 热键
        self.hotkey_manager.set_hotkey(self.config.hotkey)
        self.hotkey_manager.start_listener(
            on_press=lambda: self._async_task(self._start_recording()),
            on_release=lambda: self._async_task(self._stop_recording()),
        )
        # 权限自检:启动 5.5 秒后若无任何键盘事件,提示 macOS 辅助功能授权
        # (pynput 在未授权时静默失败,不报错也不收事件;自检窗口 5 秒)
        threading.Timer(
            5.5,
            self._check_hotkey_permission,
        ).start()
        # 麦克风权限自检:macOS 录音需要"麦克风"权限,提前探测并提示
        threading.Timer(
            6.0,
            self._check_mic_permission,
        ).start()

        # 自动连接
        self._async_task(self._connect())

        # 主事件循环
        while self.is_running:
            try:
                if not _window:
                    break
                event, values = _window.read(timeout=100)
                # 驱动悬浮指示器事件(音量条/计时器实时更新)
                if getattr(self, "indicators", None):
                    self.indicators.process_events(timeout=100)
                if event == sg.WIN_CLOSED or event == "-EXIT-":
                    break
                self._handle_event(event, values, _window)
            except Exception as e:
                logger.error(f"Main loop error: {e}")

        self._cleanup()

    def _toggle_auto_start(self):
        """切换开机自启动(托盘菜单项)"""
        try:
            if self.tray and self.tray.auto_start_manager:
                enabled = self.tray.auto_start_manager.toggle()
                self.tray.set_auto_start(enabled)
        except Exception as e:
            logger.warning(f"切换开机自启动失败: {e}")

    def _handle_event(self, event, values, window):
        if event == "-MICROPHONE-":
            # 用户选择麦克风(Combo 值是设备名 → 反查设备 id)
            name = values.get("-MICROPHONE-")
            if name:
                for dev_id, dev_name in self._audio_devices.items():
                    if dev_name == name:
                        self.selected_mic = int(dev_id) if dev_id != -1 else None
                        break
                else:
                    self.selected_mic = None
                logger.info(f"选择麦克风: {name} -> id={self.selected_mic}")

        elif event == "-MODEL-SELECT-":
            # 下拉选择模型后立即切换(enable_events 让 PySimpleGUI 正确处理焦点)
            name = values.get("-MODEL-SELECT-")
            if name:
                self._async_task(self._switch_model(name))

        elif event == "-LLM-MODEL-SELECT-":
            name = values.get("-LLM-MODEL-SELECT-")
            if name:
                self._async_task(self._switch_llm_model(name))

        elif event == "-CONNECT-":
            self.server_host = values.get("-HOST-") or self.server_host
            port_str = values.get("-PORT-") or str(self.server_port)
            try:
                self.server_port = int(port_str)
            except ValueError:
                return
            self.stt = SttClient(self.server_host, self.server_port)
            self._async_task(self._connect())

        elif event == "-REFRESH-MODELS-":
            self._async_task(self._fetch_models())

        elif event == "-SWITCH-MODEL-":
            name = values.get("-MODEL-SELECT-")
            if name:
                self._async_task(self._switch_model(name))

        elif event == "-REFRESH-LLM-MODELS-":
            self._async_task(self._fetch_llm_models())

        elif event == "-SWITCH-LLM-MODEL-":
            name = values.get("-LLM-MODEL-SELECT-")
            if name:
                self._async_task(self._switch_llm_model(name))

        elif event == "-LLM-ENABLED-":
            self.config.llm_enabled = values["-LLM-ENABLED-"]
            self.config.save()

        elif event == "-LOAD-PROMPT-":
            self._async_task(self._load_prompt())

        elif event == "-SAVE-PROMPT-":
            self._async_task(self._save_prompt(values.get("-LLM-PROMPT-", "")))

        elif event == "-UPDATE-HOTKEY-":
            hotkey = values.get("-HOTKEY-") or self.config.hotkey
            self.hotkey_manager.set_hotkey(hotkey)
            window["-HOTKEY-"].update(hotkey)
            self.config.hotkey = hotkey
            self.config.save()
            self.window.log(f"快捷键已更新: {hotkey}")

        elif event == "-RECORD-HOTKEY-":
            self.hotkey_manager.start_recording(lambda k: window["-HOTKEY-"].update(k))

        elif event == "-CLEAR-HOTKEY-":
            window["-HOTKEY-"].update("")

        elif event == "-HOTKEY-PRESET-":
            name = values.get("-HOTKEY-PRESET-")
            if name:
                preset = HotkeyPresets.get_preset(name)
                if preset:
                    window["-HOTKEY-"].update(preset["hotkey"])
                    self.window.log(f"预设 {name} 已应用: {preset['hotkey']}")

        elif event == "-APPLY-PRESET-":
            self._handle_event("-UPDATE-HOTKEY-", values, window)

        elif event == "-REC-STARTED-":
            self._on_recording_started()

        elif event == "-REC-STOPPED-":
            self._on_recording_stopped()
            self._async_task(self._process_audio())

        elif event == "-COPY-":
            result = window["-RESULT-"].get()
            if result:
                import subprocess

                subprocess.run(["pbcopy"], input=result.encode("utf-8"))
                self.window.log("已复制到剪贴板")

        elif event == "-AUTO-INPUT-":
            # 识别结果自动输入:先复制到剪贴板,再粘贴回原输入框
            text = values.get("-AUTO-INPUT-") or ""
            if text:
                self._copy_to_clipboard(text)
                self._auto_input_text(text)

        elif event == "-CLEAR-":
            window["-RESULT-"].update("")

        elif event == "-PASTE-":
            text = window["-RESULT-"].get()
            self._auto_input_text(text)

        elif event == "-MINIMIZE-TRAY-":
            if sys.platform == "darwin":
                # macOS:最小化到 Dock(iconify,点击 Dock 图标原生恢复)
                window.minimize()
            else:
                # Windows/Linux:最小化到系统托盘
                window.hide()
                if self.tray:
                    self.tray.start()

        elif event == "-SHOW-WINDOW-":
            window.un_hide()

        elif event == "-DISTINGUISH-LR-":
            val = values.get("-DISTINGUISH-LR-")
            self.config.distinguish_left_right = val
            self.config.save()

        elif event == "-USE-INDICATOR-":
            val = values.get("-USE-INDICATOR-")
            self.config.use_floating_indicator = val
            self.config.save()

        elif event == "-START-MINIMIZED-":
            val = values.get("-START-MINIMIZED-")
            self.config.start_minimized = val
            self.config.save()

        elif event == "-CHECK-UPDATE-":
            self.window.write_event_value("-DONE-UPDATE-", "")
            self._async_task(self._check_update())

    async def _check_update(self):
        from client.update_checker import check_for_updates, format_version_message

        try:
            # check_for_updates 是同步函数(内部用 urllib),不可 await
            result = check_for_updates()
            if result:
                msg = format_version_message(result)
                self.window.log(msg)
            else:
                self.window.log("已是最新版本")
        except Exception as e:
            self.window.log(f"检查更新失败: {e}")

    def _async_task(self, coro):
        if self.async_loop:
            asyncio.run_coroutine_threadsafe(coro, self.async_loop)

    def _check_mic_permission(self):
        """麦克风权限自检(延迟执行,不阻塞启动)

        macOS 录音需要"麦克风"权限(sounddevice 采集);未授权时输入流静音或无数据。
        """
        try:
            perm = self.audio.check_mic_permission()
            if perm is True:
                logger.info("麦克风权限正常")
            elif perm is False:
                msg = (
                    "⚠️ 麦克风可能未授权:录音将无声音。\n"
                    "请到 系统设置 → 隐私与安全性 → 麦克风,\n"
                    "为当前终端/应用开启权限后重启。"
                )
                logger.warning(msg.replace("\n", " "))
                if self.window:
                    self.window.log(msg)
        except Exception as e:
            logger.debug(f"麦克风权限自检失败: {e}")

    def _check_hotkey_permission(self):
        """快捷键监听权限自检(延迟执行,不阻塞启动)

        macOS 上 pynput 键盘监听需要"输入监控"(Input Monitoring)权限;
        未授权时静默收不到事件。优先用系统 API 直接检测,否则回退到事件活动检测。
        """
        try:
            # 优先:macOS 系统 API 直接查询输入监控权限
            perm = self.hotkey_manager.check_macos_permission()
            if perm is False:
                self._warn_hotkey_permission()
                return
            if perm is True:
                logger.info("输入监控权限已授权")
                return
            # 系统 API 不可用(非 macOS/旧系统):回退到事件活动检测
            if self.hotkey_manager.check_listener_activity():
                logger.info("快捷键监听正常(已收到键盘事件)")
                return
            self._warn_hotkey_permission()
        except Exception as e:
            logger.debug(f"快捷键权限自检失败: {e}")

    def _warn_hotkey_permission(self):
        """提示用户授权 macOS 输入监控权限"""
        msg = (
            "⚠️ 快捷键监听未授权:pynput 需要 macOS「输入监控」权限。\n"
            "请到 系统设置 → 隐私与安全性 → 输入监控,\n"
            "点 + 添加 终端/应用程序(或 /usr/bin/python3)并开启,\n"
            "然后重启本程序。"
        )
        logger.warning(msg.replace("\n", " "))
        if self.window:
            self.window.log(msg)
            self.window.set_status("快捷键未授权", "red")

    def _cleanup(self):
        self.is_running = False
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.async_loop:
            self.async_loop.call_soon_threadsafe(self.async_loop.stop)
        if hasattr(self, "window") and self.window and hasattr(self.window, "_window"):
            self.window.close()
        logger.info("客户端已关闭")
