#!/usr/bin/env python3
"""
Voice Input Framework - 快捷键驱动的语音输入客户端 v1.1

按住快捷键说话，松开快捷键后自动将识别结果输入到当前窗口。

功能：
- 快捷键控制（默认 Right Alt+V，支持左右修饰键区分）
- 实时录音
- WebSocket 流式识别
- 自动将结果输入活跃窗口
- 模型动态切换
- 错误信息实时显示
- 麦克风选择
- 系统托盘支持
- 悬浮录音指示器

要求:
pip install pynput sounddevice websockets httpx pyautogui pyperclip PySimpleGUI pystray Pillow
"""

import asyncio
import base64
import json
import logging
import queue
import threading
import time
from datetime import datetime
from typing import Optional
import PySimpleGUI as sg
import numpy as np

# 焦点管理（Windows）
try:
    import ctypes
    import win32gui
    import win32con
    WINAPI_AVAILABLE = True
except ImportError:
    WINAPI_AVAILABLE = False

# 导入新模块
from .hotkey_manager import HotkeyManager, HotkeyParser, HotkeyPresets
from .tray_manager import TrayIconManager, TrayStatus
from .floating_indicator import FloatingIndicator, ProcessingIndicator
from .config_manager import ConfigManager
from .update_checker import check_for_updates, format_version_message, CURRENT_VERSION
from .auto_start import AutoStartManager

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

BACKGROUND_COLOR = "#2e2e2e"
TITLE_TEXT_COLOR = "#ffcc66"
GROUP_TEXT_COLOR = "#66ccff"
TEXT_COLOR = "#66ccff"
TIP_TEXT_COLOR = "#cccccc"
BUTTON_COLOR = ("white", "gray")

# 音频参数
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_SIZE = 1024


# 焦点管理函数
def get_foreground_window():
    """获取当前活跃窗口句柄"""
    if WINAPI_AVAILABLE:
        try:
            return win32gui.GetForegroundWindow()
        except Exception as e:
            logger.debug(f"获取焦点窗口失败: {e}")
    return None


def restore_focus(hwnd):
    """恢复给定窗口的焦点"""
    if not hwnd or not WINAPI_AVAILABLE:
        return False
    
    try:
        # 使用 SetForegroundWindow 恢复焦点
        win32gui.SetForegroundWindow(hwnd)
        logger.debug(f"焦点已恢复到窗口 {hwnd}")
        return True
    except Exception as e:
        logger.debug(f"恢复焦点失败: {e}")
        return False


def restore_focus_later(hwnd, delay_ms: int = 100):
    """延迟一段时间后恢复焦点"""
    def _restore():
        time.sleep(delay_ms / 1000.0)
        restore_focus(hwnd)
    
    thread = threading.Thread(target=_restore, daemon=True)
    thread.start()


def get_input_cursor_position():
    """
    获取合适的光标位置显示浮标
    对于输入框，在鼠标位置上方显示（假设用户在该位置输入）
    这是最可靠的跨应用方式
    """
    try:
        # 获取现在的鼠标位置
        # 这是最可靠的方式，因为用户通常在鼠标位置输入
        import pyautogui
        x, y = pyautogui.position()
        # 将浮标显示在鼠标上方（而不是旁边）
        # 这样更接近输入框光标的位置
        return (x, y - 20)
    except Exception as e:
        logger.debug(f"获取光标位置失败: {e}")
        # 无法获取鼠标位置，返回默认位置
        return None


class HotkeyVoiceInputV2:
    """快捷键驱动的语音输入客户端 v1.1 - 支持左右修饰键、托盘、悬浮指示器"""

    def __init__(self, server_host: str = None, server_port: int = None):
        # 加载配置
        self.config_manager = ConfigManager()
        
        # 从配置或参数获取服务器设置
        self.server_host = server_host or self.config_manager.server_host
        self.server_port = server_port or self.config_manager.server_port
        self.server_url = f"ws://{self.server_host}:{self.server_port}/ws/stream"
        self.rest_api_url = f"http://{self.server_host}:{self.server_port}"

        # 状态
        self.is_running = False
        self.is_recording = False
        self.is_connected = False
        self.audio_buffer = []
        self.last_result = ""
        self.available_models = []  # 可用的模型列表
        self.current_model = None  # 当前模型

        # 资源
        self.stream = None
        self.ws = None
        self.window = None

        # 后台线程
        self.hotkey_listener = None
        self.async_loop = None
        self.loop_thread = None

        # 快捷键状态追踪
        self._hotkey_pressed = False
        self._pressed_keys = set()

        # 麦克风设置
        self.audio_devices = self._get_audio_devices()
        self.selected_device = None  # None 表示用默认设备

        # ======== v1.1 新增功能 ========
        # 快捷键管理器
        self.hotkey_manager = HotkeyManager(distinguish_left_right=self.config_manager.distinguish_left_right)
        self.hotkey_manager.set_hotkey(self.config_manager.hotkey)

        # 系统托盘
        self.tray_manager = TrayIconManager()
        self.use_tray = self.config_manager.use_tray  # 是否使用系统托盘
        self.is_minimized_to_tray = False
        
        # 开机自启动管理器
        self.auto_start_manager = AutoStartManager()

        # 悬浮录音指示器
        # 创建音量回调函数
        def get_audio_level():
            """获取当前音频电平"""
            try:
                if not self.audio_buffer:
                    return 0, 0
                # 获取最后一个音频块（最新的音频数据）
                last_chunk = self.audio_buffer[-1] if self.audio_buffer else b''
                if not last_chunk:
                    return 0, 0
                
    
                # 将字节转换为 numpy 数组
                audio_data = np.frombuffer(last_chunk, dtype=np.int16)
                if len(audio_data) == 0:
                    return 0, 0
                
                # 计算 RMS（均方根）音量，转换为 dB
                rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
                # 转换为 dB（0-100 范围）
                db = min(100, max(0, int((np.log10(max(rms, 1)) / 5) * 100)))
                return db, db
            except Exception as e:
                logger.debug(f"获取音频电平失败: {e}")
                return 0, 0
        
        self.floating_indicator = FloatingIndicator(follow_mouse=False, audio_callback=get_audio_level)
        self.processing_indicator = ProcessingIndicator(follow_mouse=False)
        self.use_floating_indicator = True  # 是否使用悬浮指示器
        # ================================

        self._setup_ui()

    def _get_audio_devices(self):
        """获取系统中可用的音频输入设备"""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            # 过滤出输入设备
            input_devices = {}
            for i, device in enumerate(devices):
                if device['max_input_channels'] > 0:
                    input_devices[i] = f"{device['name']}"
            return input_devices if input_devices else {-1: "默认设备"}
        except Exception as e:
            logger.warning(f"获取音频设备失败: {e}")
            return {-1: "默认设备"}

    def _setup_ui(self):
        """创建用户界面"""
        sg.theme("DarkBlue3")

        layout = [
            [sg.Text("🎤 Voice Input v1.1", font=("Helvetica", 14, "bold"),
                     justification="center", expand_x=True, background_color=BACKGROUND_COLOR, text_color=TITLE_TEXT_COLOR)],
            [sg.HorizontalSeparator()],

            # 连接状态
            [sg.Text(f"服务器: {self.server_host}:{self.server_port}", size=(50, 1), background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR),
             sg.Text("未连接", key="-STATUS-", text_color="red", size=(15, 1), background_color=BACKGROUND_COLOR)],

            # ======== v1.1 快捷键设置（增强版） ========
            [sg.Frame("快捷键设置", [
                [sg.Text("开始/停止录音:", background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR),
                 sg.Input(self.config_manager.hotkey, key="-HOTKEY-", size=(30, 1)),
                 sg.Button("录制", key="-RECORD-HOTKEY-", size=(8, 1)),
                 sg.Button("更新", key="-UPDATE-HOTKEY-", size=(8, 1)),
                 sg.Button("清除", key="-CLEAR-HOTKEY-", size=(8, 1))],
                [sg.Checkbox("区分左右修饰键", default=self.config_manager.distinguish_left_right,
                            key="-DISTINGUISH-LR-", enable_events=True, background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR)],
                [sg.Text("(按住快捷键说话，松开后自动输入)",
                        text_color=TIP_TEXT_COLOR, font=("Helvetica", 9), background_color=BACKGROUND_COLOR)],
                # 快捷键预设方案
                [sg.Text("预设方案:", background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR),
                 sg.Combo(list(HotkeyPresets.get_preset_names()),
                         default_value="default", key="-HOTKEY-PRESET-",
                         size=(20, 1), readonly=True, enable_events=True),
                 sg.Button("应用预设", key="-APPLY-PRESET-", size=(10, 1))],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR, expand_x=True)],
            # ==========================================

            # 麦克风选择
            [sg.Frame("麦克风设置", [
                [sg.Text("麦克风:", background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR),
                 sg.Combo(list(self.audio_devices.values()),
                         default_value=self.audio_devices.get(list(self.audio_devices.keys())[0], "默认设备"),
                         key="-MICROPHONE-", size=(50, 1), readonly=True)],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR, expand_x=True)],

            # 服务器配置
            [sg.Frame("服务器配置", [
                [sg.Text("主机:", background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR), sg.Input(self.server_host, key="-HOST-", size=(20, 1)),
                 sg.Text("端口:", background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR), sg.Input(str(self.server_port), key="-PORT-", size=(8, 1))],
                [sg.Button("连接", key="-CONNECT-", button_color=("white", "green"), size=(10, 1)),
                 sg.Text("", key="-CONN-STATUS-", text_color="yellow", background_color=BACKGROUND_COLOR)],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR, expand_x=True)],

            # 模型选择
            [sg.Frame("模型设置", [
                [sg.Text("选择模型:", background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR),
                 sg.Combo([], default_value="", key="-MODEL-SELECT-", size=(30, 1), readonly=True),
                 sg.Button("刷新", key="-REFRESH-MODELS-", size=(8, 1)),
                 sg.Button("切换", key="-SWITCH-MODEL-", button_color=("white", "blue"), size=(8, 1))],
                [sg.Text("", key="-MODEL-STATUS-", text_color="yellow", size=(70, 1), background_color=BACKGROUND_COLOR)],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR, expand_x=True)],

            # ======== v1.1 新增：托盘和指示器设置 ========
            [sg.Frame("界面设置", [
                [sg.Checkbox("启动时最小化到托盘", default=self.config_manager.start_minimized,
                            key="-START-MINIMIZED-", enable_events=True, background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR)],
                [sg.Checkbox("使用悬浮录音指示器", default=self.config_manager.use_floating_indicator,
                            key="-USE-INDICATOR-", enable_events=True, background_color=BACKGROUND_COLOR, text_color=TEXT_COLOR),
                 sg.Button("显示主窗口", key="-SHOW-WINDOW-", size=(15, 1))],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR, expand_x=True)],
            # =============================================

            # 识别结果
            [sg.Frame("识别结果", [
                [sg.Multiline("", key="-RESULT-", size=(80, 8), font=("Consolas", 10),
                             autoscroll=True, disabled=True,
                             background_color="#1e1e1e", text_color="white")],
                [sg.Button("复制", key="-COPY-", size=(10, 1)),
                 sg.Button("清空", key="-CLEAR-", size=(10, 1)),
                 sg.Button("输入（自动）", key="-PASTE-", size=(15, 1))],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR, expand_x=True)],

            # 日志
            [sg.Frame("日志", [
                [sg.Multiline("", key="-LOG-", size=(80, 5), font=("Consolas", 10),
                             autoscroll=True, disabled=True,
                             background_color="#1e1e1e", text_color="#aaaaaa")],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR, expand_x=True)],

            # 错误信息显示
            [sg.Frame("错误信息", [
                [sg.Multiline("", key="-ERROR-", size=(80, 3), font=("Consolas", 10),
                             autoscroll=True, disabled=True,
                             background_color="#3e1e1e", text_color="#ff8888")],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR, expand_x=True)],

            [sg.Push(background_color=BACKGROUND_COLOR),
              sg.Button("退出", key="-EXIT-", button_color=("white", "gray"), size=(10, 1)),
              sg.Button("最小化到托盘", key="-MINIMIZE-TRAY-", size=(15, 1)),
                sg.Push(background_color=BACKGROUND_COLOR)],
        ]

        self.window = sg.Window("🎤 Voice Input Framework v1.1", layout,
                               finalize=True, keep_on_top=True, no_titlebar=True, grab_anywhere=True,
                               background_color="#2e2e2e", button_color=("white", "#4e4e4e")
                               )
        
        if self.config_manager.start_minimized:
            self._minimize_to_tray()

        self.is_running = True

    def log(self, message: str):
        """添加日志条目"""
        if not self.window:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.window["-LOG-"].print(f"[{timestamp}] {message}")

    def update_result(self, text: str):
        """更新识别结果"""
        if not self.window:
            return
        self.window["-RESULT-"].print(text)
        self.last_result = text

    def set_status(self, status: str, color: str = "yellow"):
        """更新连接状态"""
        if self.window:
            self.window["-STATUS-"].update(status, text_color=color)

    # ======== v1.1 新增方法 ========

    def _setup_tray(self):
        """设置系统托盘"""
        if not self.use_tray:
            return

        # 设置托盘回调
        self.tray_manager.set_callback("show_window", self._show_window_from_tray)
        self.tray_manager.set_callback("hide_window", self._minimize_to_tray)
        self.tray_manager.set_callback("start_recording", self._start_recording_from_tray)
        self.tray_manager.set_callback("stop_recording", self._stop_recording_from_tray)
        self.tray_manager.set_callback("switch_model", self._switch_model_from_tray)
        self.tray_manager.set_callback("refresh_models", self._refresh_models_from_tray)
        self.tray_manager.set_callback("check_update", self._check_for_updates)
        self.tray_manager.set_callback("toggle_auto_start", self._toggle_auto_start)
        self.tray_manager.set_callback("quit", self._quit_from_tray)

        # 设置模型列表
        self.tray_manager.set_available_models(self.available_models)
        self.tray_manager.set_current_model(self.current_model or "")
        
        # 设置开机自启动状态
        auto_start_enabled = self.auto_start_manager.is_enabled()
        self.tray_manager.set_auto_start_enabled(auto_start_enabled)

        # 启动托盘
        self.tray_manager.start()

    def _minimize_to_tray(self):
        """最小化到托盘"""
        if self.window:
            self.window.hide()
            self.is_minimized_to_tray = True
            self.log("已最小化到系统托盘")

    def _show_window_from_tray(self):
        """从托盘显示窗口"""
        if self.window and self.is_minimized_to_tray:
            self.window.un_hide()
            self.is_minimized_to_tray = False
            self.log("已显示主窗口")

    def _start_recording_from_tray(self):
        """从托盘开始录音"""
        if not self.is_recording:
            self._start_recording()

    def _stop_recording_from_tray(self):
        """从托盘停止录音"""
        if self.is_recording:
            self._stop_recording()
            # 流式传输已在 _stop_recording() 中触发，会自动处理结果

    def _switch_model_from_tray(self, model_name: str):
        """从托盘切换模型"""
        if self.async_loop:
            asyncio.run_coroutine_threadsafe(
                self.async_switch_model(model_name),
                self.async_loop
            )

    def _refresh_models_from_tray(self):
        """从托盘刷新模型列表"""
        if self.async_loop:
            asyncio.run_coroutine_threadsafe(
                self.async_fetch_models(),
                self.async_loop
            )

    def _quit_from_tray(self):
        """从托盘退出"""
        self.is_running = False
        if self.window:
            self.window.close()

    def _show_indicator_with_focus_preservation(self, indicator, cursor_pos=None):
        """显示指示器并保留焦点"""
        focus_hwnd = get_foreground_window()
        if cursor_pos is None:
            cursor_pos = get_input_cursor_position()
        indicator.show(cursor_pos=cursor_pos)
        if focus_hwnd:
            restore_focus_later(focus_hwnd, delay_ms=50)

    def _show_startup_notification(self):
        """显示启动通知"""
        hotkey = self.config_manager.hotkey
        version = "v1.1.0"
        
        # 托盘通知
        if self.tray_manager:
            self.tray_manager.notify(
                "Voice Input Framework",
                f"已就绪！快捷键: {hotkey}"
            )
        
        # 同时显示在状态栏
        self.log(f"✓ Voice Input Framework {version} 已启动")
        self.log(f"✓ 快捷键: {hotkey}")
        self.log(f"✓ 服务器: {self.server_host}:{self.server_port}")

    def _check_for_updates(self):
        """检查更新（从托盘菜单调用）"""
        self.log("正在检查更新...")
        
        try:
            version_info = check_for_updates()
            message = format_version_message(version_info)
            
            self.log(message)
            
            # 如果有更新，显示通知
            if version_info.is_outdated and self.tray_manager:
                self.tray_manager.notify(
                    "发现新版本",
                    f"{version_info.latest_version} 可用，点击下载"
                )
                
        except Exception as e:
            self.log(f"检查更新失败: {e}")

    def _toggle_auto_start(self):
        """切换开机自启动状态"""
        try:
            new_state = self.auto_start_manager.toggle()
            self.tray_manager.set_auto_start_enabled(new_state)
            
            if new_state:
                self.log("✓ 开机自启动已启用")
            else:
                self.log("✓ 开机自启动已禁用")
        except Exception as e:
            self.log(f"切换开机自启动失败: {e}")

    def _setup_hotkey_with_manager(self, hotkey_str: str):
        """使用新的快捷键管理器设置快捷键"""
        try:
            self.hotkey_manager.set_hotkey(hotkey_str)
            self.hotkey_manager.start_listener(
                on_press=self._on_hotkey_press,
                on_release=self._on_hotkey_release
            )
            self.log(f"✓ 快捷键已激活: {hotkey_str}")
        except Exception as e:
            self.log(f"✗ 快捷键设置失败: {e}")

    def _on_hotkey_press(self):
        """快捷键按下回调 - 线程安全版本
        
        注意：此方法在 pynput 的后台线程中调用，不能直接操作 GUI。
        使用 write_event_value 将事件发送到主线程处理。
        """
        if self.window:
            self.window.write_event_value("-HOTKEY-PRESS-", None)

    def _on_hotkey_release(self):
        """快捷键释放回调 - 线程安全版本
        
        注意：此方法在 pynput 的后台线程中调用，不能直接操作 GUI。
        使用 write_event_value 将事件发送到主线程处理。
        """
        if self.window:
            self.window.write_event_value("-HOTKEY-RELEASE-", None)

 # 注意：音频处理在主线程的 -HOTKEY-RELEASE- 事件处理中执行
 # 不在这里直接调用 _process_audio()，避免线程安全问题

    def _on_hotkey_recorded(self, hotkey_str: str):
        """快捷键录制完成回调"""
        self.window["-HOTKEY-"].update(hotkey_str)
        self.log(f"录制到快捷键: {hotkey_str}")

    # ================================

    async def connect_to_server(self) -> bool:
        """连接到服务器并验证连接"""
        try:
            import websockets
            self.log(f"连接到 {self.server_url}...")
            self.set_status("连接中...", "yellow")

            # 创建临时连接来测试
            self.ws = await asyncio.wait_for(
                websockets.connect(self.server_url, close_timeout=5),
                timeout=10.0
            )

            # 等待服务器准备就绪
            ready_msg = await asyncio.wait_for(self.ws.recv(), timeout=30.0)
            data = json.loads(ready_msg)

            if data.get("type") == "ready":
                model = data.get("model", "unknown")
                is_loading = data.get("is_loading", False)
                self.log(f"✓ 已连接，服务器模型: {model}")

                if is_loading:
                    self.log(f"⚠️ 模型 {model} 正在加载中，切换模型可能会有延迟")
                    self.set_status(f"已连接 ({model} 加载中...)", "yellow")
                    if self.tray_manager:
                        self.tray_manager.set_status(TrayStatus.PROCESSING)
                else:
                    self.set_status(f"已连接 ({model})", "green")
                    if self.tray_manager:
                        self.tray_manager.set_status(TrayStatus.READY)

                self.is_connected = True
                self.current_model = model

                # 更新托盘模型信息
                if self.tray_manager:
                    self.tray_manager.set_current_model(model)

                # 关闭测试连接，后续会为每次转写创建新连接
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None

                # 自动获取模型列表
                await self.fetch_models()
                return True
            else:
                self.log(f"✗ 服务器响应错误: {data}")
                self.set_status("连接失败", "red")
                if self.tray_manager:
                    self.tray_manager.set_status(TrayStatus.ERROR)
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None
                return False

        except asyncio.TimeoutError:
            self.log("✗ 连接超时")
            self.set_status("连接超时", "red")
            if self.tray_manager:
                self.tray_manager.set_status(TrayStatus.ERROR)
            return False
        except Exception as e:
            self.log(f"✗ 连接失败: {e}")
            self.set_status("连接失败", "red")
            if self.tray_manager:
                self.tray_manager.set_status(TrayStatus.ERROR)
            return False

    async def fetch_models(self):
        """获取服务器上的可用模型列表"""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{self.rest_api_url}/models"
                self.log(f"正在获取模型列表 from {url}...")

                try:
                    resp = await client.get(url)
                    self.log(f"模型列表响应状态: {resp.status_code}")

                    if resp.status_code == 200:
                        try:
                            response_data = resp.json()
                            self.log(f"原始响应数据: {response_data}")

                            # 处理响应格式
                            if isinstance(response_data, list):
                                # 列表格式
                                self.available_models = []
                                for m in response_data:
                                    if isinstance(m, dict):
                                        name = m.get("name", "")
                                        if name:
                                            self.available_models.append(name)
                                        if m.get("is_loaded", False):
                                            self.current_model = name
                            elif isinstance(response_data, dict):
                                # 字典格式，可能带有 "models" 键
                                if "models" in response_data:
                                    self.available_models = [m.get("name", "")
                                                            for m in response_data.get("models", [])]
                                    for m in response_data.get("models", []):
                                        if m.get("is_loaded", False):
                                            self.current_model = m.get("name", "")
                                            break
                                else:
                                    self.available_models = []
                            else:
                                self.available_models = []

                            # 过滤掉空字符串
                            self.available_models = [m for m in self.available_models if m]

                            if self.available_models:
                                self.log(f"✓ 获取到模型列表: {', '.join(self.available_models)}")
                            else:
                                self.log(f"⚠️ 响应中未找到模型，响应完整内容: {response_data}")
                                self.show_error(f"未找到可用的模型。服务器响应: {response_data}")

                            # 更新UI下拉菜单
                            if self.window and self.available_models:
                                self.window["-MODEL-SELECT-"].update(
                                    values=self.available_models,
                                    value=self.current_model
                                )
                                self.window["-MODEL-STATUS-"].update(
                                    f"当前模型: {self.current_model}",
                                    text_color="yellow"
                                )
                                # 更新托盘
                                if self.tray_manager:
                                    self.tray_manager.set_available_models(self.available_models)
                                    self.tray_manager.set_current_model(self.current_model or "")
                            elif self.window:
                                self.log("没有可用的模型")
                                self.window["-MODEL-SELECT-"].update(values=[], value="")
                                self.window["-MODEL-STATUS-"].update("未找到可用模型", text_color="red")

                            return bool(self.available_models)

                        except json.JSONDecodeError as e:
                            self.log(f"✗ JSON 解析失败: {e}")
                            self.log(f"响应内容: {resp.text}")
                            self.show_error(f"响应不是有效 JSON: {resp.text[:200]}")
                            return False
                    else:
                        error_text = resp.text
                        self.log(f"✗ 获取模型失败: HTTP {resp.status_code}")
                        self.log(f"错误响应: {error_text}")
                        self.show_error(f"获取模型失败: HTTP {resp.status_code}\n{error_text[:200]}")
                        return False

                except Exception as e:
                    import traceback
                    self.log(f"✗ HTTP 请求失败: {e}")
                    self.log(f"错误堆栈: {traceback.format_exc()}")
                    self.show_error(f"HTTP 请求失败: {e}")
                    return False

        except ImportError:
            self.log("⚠️ 需要安装 httpx: pip install httpx")
            self.show_error("需要安装 httpx:\npip install httpx")
            return False
        except Exception as e:
            import traceback
            self.log(f"✗ 获取模型失败: {e}")
            self.log(f"错误堆栈: {traceback.format_exc()}")
            self.show_error(f"获取模型失败: {e}")
            return False

    async def switch_model(self, model_name: str):
        """切换模型"""
        try:
            import httpx
            # 大幅增加超时时间以允许 qwen_asr 模型（14GB）加载
            async with httpx.AsyncClient(timeout=300.0) as client:  # 5 分钟超时
                url = f"{self.rest_api_url}/models/select"
                data = {"model_name": model_name}

                try:
                    self.log(f"正在切换到模型: {model_name}，请等待（qwen_asr 模型较大，需几分钟）...")
                    if self.window:
                        self.window["-MODEL-STATUS-"].update(
                            f"正在切换到 {model_name}...（请等待）",
                            text_color="yellow"
                        )

                    resp = await client.post(url, data=data)

                    if resp.status_code == 200:
                        result = resp.json()
                        self.current_model = model_name
                        is_loading = result.get("is_loading", False)

                        if is_loading:
                            self.log(f"✓ 切换请求已接受，模型 {model_name} 正在后台加载")
                            if self.window:
                                self.window["-MODEL-STATUS-"].update(
                                    f"模型 {model_name} 正在加载中...",
                                    text_color="yellow"
                                )
                        else:
                            self.log(f"✓ 已切换到模型: {model_name}")
                            if self.window:
                                self.window["-MODEL-STATUS-"].update(
                                    f"当前模型: {model_name}",
                                    text_color="green"
                                )
                            if self.tray_manager:
                                self.tray_manager.set_current_model(model_name)

                        return True
                    elif resp.status_code == 408:  # Timeout
                        self.log(f"✗ 切换模型超时: 模型加载时间过长")
                        self.show_error(f"切换模型超时\n{model_name} 模型太大，加载时间超过 5 分钟")
                        return False
                    else:
                        error_text = resp.text
                        self.log(f"✗ 切换模型失败: HTTP {resp.status_code}")
                        self.show_error(f"切换模型失败: HTTP {resp.status_code}\n{error_text}")
                        return False

                except Exception as e:
                    import traceback
                    self.log(f"✗ HTTP 请求失败: {type(e).__name__}: {e}")
                    self.log(f"错误堆栈: {traceback.format_exc()}")
                    self.show_error(f"HTTP 请求失败: {type(e).__name__}: {e}")
                    return False

        except ImportError:
            self.log("⚠️ 需要安装 httpx: pip install httpx")
            self.show_error("需要安装 httpx:\npip install httpx")
            return False
        except Exception as e:
            self.log(f"✗ 切换模型失败: {e}")
            self.show_error(f"切换模型失败: {e}")
            return False

    def show_error(self, message: str):
        """显示错误信息"""
        if not self.window:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.window["-ERROR-"].print(f"[{timestamp}] ❌ {message}")

    async def async_fetch_models(self):
        """异步获取模型列表（在事件循环中执行）"""
        await self.fetch_models()

    async def async_switch_model(self, model_name: str):
        """异步切换模型（在事件循环中执行）"""
        await self.switch_model(model_name)

    async def send_audio_to_server(self) -> Optional[str]:
        """发送音频到服务器并获取识别结果"""
        if not self.audio_buffer:
            self.log("没有音频数据")
            return None

        if not self.is_connected:
            self.log("未连接到服务器，正在重新连接...")
            if not await self.connect_to_server():
                return None

        try:
            import websockets

            # 创建新的WebSocket连接用于此次转写
            self.log("正在连接到服务器...")
            ws = await asyncio.wait_for(
                websockets.connect(self.server_url, close_timeout=10),
                timeout=15.0
            )

            # 等待服务器准备就绪
            self.log("等待服务器准备就绪...")
            ready_msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
            data = json.loads(ready_msg)

            if data.get("type") != "ready":
                self.log(f"服务器没有准备就绪: {data}")
                await ws.close()
                return None

            ready_model = data.get("model", "unknown")
            is_loading = data.get("is_loading", False)
            self.log(f"服务器准备就绪，当前模型: {ready_model}")

            if is_loading:
                self.log("⚠️ 模型正在加载中，可能需要等待...")

            # 发送配置消息（服务器期望的第一条消息）
            await ws.send(json.dumps({
                "type": "config",
                "language": "auto"
            }))

            # 合并音频数据
            full_audio = b"".join(self.audio_buffer)
            audio_size_kb = len(full_audio) / 1024
            self.log(f"发送 {audio_size_kb:.1f} KB 音频...")

            # 发送音频消息
            await ws.send(json.dumps({
                "type": "audio",
                "data": base64.b64encode(full_audio).decode()
            }))

            # 发送结束信号
            await ws.send(json.dumps({"type": "end"}))

            # 接收结果 - qwen_asr 模型很大，增加超时时间到 5 分钟
            self.log("等待识别结果...")
            result_text = ""

            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=300.0)  # 5 分钟超时
                    data = json.loads(response)
                    msg_type = data.get("type")

                    if msg_type == "result":
                        result_text = data.get("text", "")
                        self.log(f"识别结果: {result_text}")
                    elif msg_type == "done":
                        self.log("识别完成")
                        await ws.close()
                        return result_text
                    elif msg_type == "error":
                        error_msg = data.get("error_message", "未知错误")
                        error_code = data.get("error_code", "")
                        self.log(f"识别错误 [{error_code}]: {error_msg}")
                        await ws.close()
                        return None

                except asyncio.TimeoutError:
                    self.log("识别超时（5分钟） - 模型可能还在加载中")
                    await ws.close()
                    return None

        except asyncio.TimeoutError:
            self.log("连接超时")
        except Exception as e:
            self.log(f"发送音频失败: {e}")
            return None

    def _start_recording(self):
        """开始录音并建立 WebSocket 流式传输"""
        import sounddevice as sd

        self.is_recording = True
        self.audio_buffer = []  # 保留用于备用
        self.audio_queue = queue.Queue()  # 音频块队列，用于边录边发
        self.stream_result = None  # 存储识别结果
        self.stream_error = None  # 存储错误信息
        self.log("🔴 开始录音...")

        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            if self.is_recording:
                audio_bytes = indata.copy().tobytes()
                self.audio_buffer.append(audio_bytes)  # 存入 buffer
                try:
                    self.audio_queue.put_nowait(audio_bytes)  # 存入队列供发送
                except queue.Full:
                    pass  # 队列满时丢弃旧数据

        try:
            self.stream = sd.InputStream(
                device=self.selected_device,
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype='int16',
                blocksize=AUDIO_CHUNK_SIZE,
                callback=callback
            )
            self.stream.start()
            
            # 记录录音开始时间
            self._record_start_time = time.time()
            
            # 启动流式发送协程
            if self.async_loop:
                asyncio.run_coroutine_threadsafe(
                    self._stream_audio_to_server(),
                    self.async_loop
                )
                
        except Exception as e:
            self.log(f"启动录音失败: {e}")
            self.is_recording = False

    def _stop_recording(self):
        """停止录音并发送结束信号"""
        self.is_recording = False
        chunks_count = len(self.audio_buffer)
        # 计算录音时长
        if hasattr(self, '_record_start_time') and self._record_start_time:
            record_duration = time.time() - self._record_start_time
            self.log(f"⏹️ 停止录音 ({chunks_count} 个音频块, 录音时长: {record_duration:.1f}s)")
        else:
            self.log(f"⏹️ 停止录音 ({chunks_count} 个音频块)")

        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.warning(f"关闭音频流失败: {e}")
            self.stream = None
        
        # 通知发送协程结束
        try:
            self.audio_queue.put_nowait(None)  # None 表示结束
        except:
            pass

    async def _stream_audio_to_server(self):
        """流式发送音频到服务器（边录边发）"""
        import websockets
        
        try:
            self.log("建立 WebSocket 连接...")
            
            async with websockets.connect(self.server_url, close_timeout=10) as ws:
                # 发送配置
                language = self.config_manager.get('audio.language', 'auto')
                await ws.send(json.dumps({
                    "type": "config",
                    "language": language
                }))
                self.log(f"已发送配置 (language: {language})")
                
                # 等待准备就绪
                try:
                    ready_msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    data = json.loads(ready_msg)
                    if data.get("type") == "ready":
                        self.log("服务器已准备就绪，开始流式传输...")
                    else:
                        self.log(f"服务器响应异常: {data}")
                except asyncio.TimeoutError:
                    self.log("等待服务器准备超时")
                    return
                
                # 流式发送音频块
                while self.is_recording or not self.audio_queue.empty():
                    try:
                        # 从队列获取音频块（最多等待 0.1 秒）
                        chunk = self.audio_queue.get(timeout=0.1)
                        
                        if chunk is None:  # 收到结束信号
                            break
                            
                        # 发送音频块
                        await ws.send(json.dumps({
                            "type": "audio",
                            "data": base64.b64encode(chunk).decode()
                        }))
                        
                    except queue.Empty:
                        # 队列为空但还在录音，继续等待
                        continue
                    except Exception as e:
                        self.log(f"发送音频块出错: {e}")
                        break
                
                # 发送结束信号
                self.log("发送结束信号...")
                await ws.send(json.dumps({"type": "end"}))
                
                # 等待识别结果
                self.log("等待识别结果...")
                result_text = ""
                
                while True:
                    try:
                        response = await asyncio.wait_for(ws.recv(), timeout=300.0)
                        data = json.loads(response)
                        msg_type = data.get("type")
                        
                        if msg_type == "result":
                            result_text = data.get("text", "")
                            self.log(f"识别结果: {result_text}")
                        elif msg_type == "done":
                            self.log("识别完成")
                            self.stream_result = result_text
                            break
                        elif msg_type == "error":
                            error_msg = data.get("error_message", "未知错误")
                            error_code = data.get("error_code", "")
                            self.log(f"识别错误 [{error_code}]: {error_msg}")
                            self.stream_error = error_msg
                            break
                            
                    except asyncio.TimeoutError:
                        self.log("等待结果超时")
                        break
                        
        except Exception as e:
            self.log(f"流式传输出错: {e}")
            self.stream_error = str(e)
        finally:
            # 在主线程处理结果
            if self.async_loop:
                asyncio.run_coroutine_threadsafe(
                    self._handle_stream_result(),
                    self.async_loop
                )

    async def _handle_stream_result(self):
        """处理流式传输的结果（在主线程中调用）"""
        # 计算总处理时间
        total_time = time.time() - self._record_start_time if hasattr(self, '_record_start_time') and self._record_start_time else 0
        
        if self.stream_error:
            self.log(f"流式识别失败: {self.stream_error}")
            print(f"[耗时统计] 录音: {total_time:.1f}s, 错误: {self.stream_error}")
            if self.tray_manager:
                self.tray_manager.set_status(TrayStatus.ERROR)
            if self.processing_indicator:
                self.processing_indicator.hide()
            return
        
        result = self.stream_result
        
        if result:
            self.update_result(result)
            await self._auto_input_text(result)
            print(f"[耗时统计] 录音: {total_time:.1f}s, 结果: {result[:50]}...")
            
            if self.tray_manager:
                self.tray_manager.set_status(TrayStatus.READY)
        else:
            self.log("未收到识别结果")
            print(f"[耗时统计] 录音: {total_time:.1f}s, 结果: (无)")
            if self.tray_manager:
                self.tray_manager.set_status(TrayStatus.ERROR)
        
        if self.processing_indicator:
            self.processing_indicator.hide()

    async def _process_audio(self):
        """处理录制的音频（备用模式：录完再发）"""
        try:
            self.log("处理音频...")

            # 更新托盘状态
            if self.tray_manager:
                self.tray_manager.set_status(TrayStatus.PROCESSING)

            result = await self.send_audio_to_server()

            if result:
                self.log(f"更新结果显示...")
                self.update_result(result)
                self.log(f"开始自动输入...")
                # 自动输入文本
                await self._auto_input_text(result)
                self.log(f"自动输入完成")

                # 更新托盘状态为就绪
                if self.tray_manager:
                    self.tray_manager.set_status(TrayStatus.READY)
            else:
                self.log("未收到识别结果")
                if self.tray_manager:
                    self.tray_manager.set_status(TrayStatus.ERROR)

            # 隐藏处理中指示器
            if self.processing_indicator:
                self.processing_indicator.hide()

        except Exception as e:
            import traceback
            self.log(f"处理音频出错: {e}")
            self.log(traceback.format_exc())
            if self.tray_manager:
                self.tray_manager.set_status(TrayStatus.ERROR)
            if self.processing_indicator:
                self.processing_indicator.hide()

    async def _auto_input_text(self, text: str):
        """自动将文本输入到活跃窗口"""
        try:
            import pyautogui
            self.log(f"准备输入文本: {text[:50]}...")

            # 使用剪贴板粘贴（更可靠，支持特殊字符）
            try:
                import pyperclip
                pyperclip.copy(text)
                self.log("✓ 文本已复制到剪贴板")

                # 粘贴文本
                pyautogui.hotkey('ctrl', 'v')
                self.log(f"✓ 已粘贴: {text[:50]}...")

            except ImportError as e:
                self.log(f"⚠️ pyperclip 不可用，使用逐字输入: {e}")
                # 回退到逐字输入
                pyautogui.typewrite(text, interval=0.01)
                self.log(f"✓ 已逐字输入: {text[:50]}...")

            except Exception as e:
                self.log(f"粘贴失败: {e}，尝试逐字输入...")
                pyautogui.typewrite(text, interval=0.01)
                self.log(f"✓ 已逐字输入: {text[:50]}...")

        except ImportError:
            self.log("⚠️ 需要安装 pyautogui 进行自动输入")
        except Exception as e:
            self.log(f"输入文本失败: {e}")

    def _run_async_loop(self):
        """在后台线程中运行异步事件循环"""
        self.async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.async_loop)
        self.async_loop.run_forever()

    def run(self):
        """主界面循环"""
        # 启动异步事件循环
        self.loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.loop_thread.start()
        time.sleep(0.1)  # 等待事件循环启动

        # 设置系统托盘
        self._setup_tray()

        # 设置初始快捷键（使用配置）
        self._setup_hotkey_with_manager(self.config_manager.hotkey)

        # 自动连接到服务器
        if self.async_loop:
            asyncio.run_coroutine_threadsafe(
                self.connect_to_server(),
                self.async_loop
            )
        
        # 显示启动通知
        self._show_startup_notification()

        # 主 UI 循环
        while self.is_running:
            try:
                # Ensure window exists before reading
                if not self.window:
                    logger.error("Window is None, breaking")
                    break
                    
                event, values = self.window.read(timeout=100)

                if event == sg.WIN_CLOSED or event == "-EXIT-":
                    break

                elif event == "-CONNECT-":
                    host = values.get("-HOST-") or self.server_host
                    port_str = values.get("-PORT-") or str(self.server_port)
                    self.server_host = host
                    try:
                        self.server_port = int(port_str)
                    except ValueError:
                        self.log("端口号必须是整数")
                        self.show_error("端口号必须是整数")
                        continue

                    self.server_url = f"ws://{self.server_host}:{self.server_port}/ws/stream"
                    self.rest_api_url = f"http://{self.server_host}:{self.server_port}"
                    self.is_connected = False

                    if self.async_loop:
                        asyncio.run_coroutine_threadsafe(
                            self.connect_to_server(),
                            self.async_loop
                        )

                elif event == "-UPDATE-HOTKEY-":
                    hotkey = values.get("-HOTKEY-", self.config_manager.hotkey).strip()
                    self._setup_hotkey_with_manager(hotkey)
                    # 保存配置
                    self.config_manager.hotkey = hotkey
                    self.config_manager.save()

                elif event == "-RECORD-HOTKEY-":
                    # 开始录制快捷键
                    self.log("请按下目标快捷键...")
                    self.hotkey_manager.start_recording(self._on_hotkey_recorded)

                elif event == "-CLEAR-HOTKEY-":
                    self.window["-HOTKEY-"].update("")
                    self.log("已清除快捷键")

                elif event == "-DISTINGUISH-LR-":
                    # 切换是否区分左右修饰键
                    distinguish = values["-DISTINGUISH-LR-"]
                    self.hotkey_manager.distinguish_left_right = distinguish
                    self.config_manager.distinguish_left_right = distinguish
                    self.config_manager.save()
                    self.log(f"已{'启用' if distinguish else '禁用'}左右修饰键区分")

                elif event == "-HOTKEY-PRESET-":
                    # 选择预设方案
                    preset_name = values["-HOTKEY-PRESET-"]
                    preset = HotkeyPresets.get_preset(preset_name)
                    if preset:
                        self.window["-HOTKEY-"].update(preset['hotkey'])
                        self.log(f"选择预设: {preset['name']}")

                elif event == "-APPLY-PRESET-":
                    # 应用预设方案
                    preset_name = values.get("-HOTKEY-PRESET-", "default")
                    preset = HotkeyPresets.get_preset(preset_name)
                    if preset:
                        hotkey = preset['hotkey']
                        self._setup_hotkey_with_manager(hotkey)
                        self.window["-HOTKEY-"].update(hotkey)
                        self.log(f"✓ 已应用预设: {preset['name']}")

                elif event == "-MICROPHONE-":
                    selected_name = values.get("-MICROPHONE-")
                    for device_id, device_name in self.audio_devices.items():
                        if device_name == selected_name:
                            self.selected_device = device_id if device_id != -1 else None
                            self.config_manager.selected_device = self.selected_device
                            self.config_manager.save()
                            self.log(f"✓ 已选择麦克风: {device_name}")
                            break

                elif event == "-COPY-":
                    if self.last_result:
                        try:
                            import pyperclip
                            pyperclip.copy(self.last_result)
                            self.log("✓ 已复制到剪贴板")
                        except ImportError:
                            self.log("需要安装 pyperclip")

                elif event == "-CLEAR-":
                    self.window["-RESULT-"].update("")
                    self.last_result = ""
                    self.log("已清空结果")

                elif event == "-PASTE-":
                    if self.last_result and self.async_loop:
                        asyncio.run_coroutine_threadsafe(
                            self._auto_input_text(self.last_result),
                            self.async_loop
                        )

                elif event == "-REFRESH-MODELS-":
                    self.log("正在获取模型列表...")
                    self.window["-MODEL-STATUS-"].update("正在获取模型列表...", text_color="yellow")
                    if self.async_loop:
                        asyncio.run_coroutine_threadsafe(
                            self.async_fetch_models(),
                            self.async_loop
                        )

                elif event == "-SWITCH-MODEL-":
                    selected_model = values.get("-MODEL-SELECT-", "").strip()
                    if not selected_model:
                        self.show_error("请先选择一个模型")
                        self.log("❌ 未选择模型")
                    else:
                        self.log(f"正在切换到模型: {selected_model}")
                        self.window["-MODEL-STATUS-"].update(
                            f"正在切换到 {selected_model}...",
                            text_color="yellow"
                        )
                        if self.async_loop:
                            asyncio.run_coroutine_threadsafe(
                                self.async_switch_model(selected_model),
                                self.async_loop
                            )

                # ======== v1.1 新增事件处理 ========
                elif event == "-START-MINIMIZED-":
                    self.config_manager.start_minimized = values['-START-MINIMIZED-']
                    self.config_manager.save()
                    self.log(f"启动最小化设置: {values['-START-MINIMIZED-']}")

                elif event == "-USE-INDICATOR-":
                    self.use_floating_indicator = values["-USE-INDICATOR-"]
                    self.config_manager.use_floating_indicator = self.use_floating_indicator
                    self.config_manager.save()
                    self.log(f"悬浮指示器: {'启用' if self.use_floating_indicator else '禁用'}")

                elif event == "-MINIMIZE-TRAY-":
                    self._minimize_to_tray()

                elif event == "-SHOW-WINDOW-":
                    self._show_window_from_tray()
                # ======== 线程安全的快捷键事件处理 ========
                elif event == "-HOTKEY-PRESS-":
                    # 在主线程中处理快捷键按下
                    try:
                        self.log("🎙️ 快捷键激活 - 开始录音!")
                        self._hotkey_pressed = True
                        self._start_recording()
                        # 更新托盘状态
                        if self.tray_manager:
                            self.tray_manager.set_status(TrayStatus.RECORDING)
                        # 显示悬浮指示器
                        if self.use_floating_indicator and self.floating_indicator:
                            try:
                                self._show_indicator_with_focus_preservation(self.floating_indicator)
                            except Exception as e:
                                logger.warning(f"Failed to show floating indicator: {e}")
                    except Exception as e:
                        logger.error(f"Error in hotkey press: {e}", exc_info=True)
                        self.log(f"❌ 快捷键处理错误: {e}")
                elif event == "-HOTKEY-RELEASE-":
                    # 在主线程中处理快捷键释放
                    try:
                        self.log("⏹️ 快捷键释放 - 停止录音!")
                        self._hotkey_pressed = False
                        self._stop_recording()
                        # 隐藏悬浮指示器
                        if self.floating_indicator:
                            try:
                                self.floating_indicator.hide()
                            except Exception as e:
                                logger.warning(f"Failed to hide floating indicator: {e}")
                        # 更新托盘状态
                        if self.tray_manager:
                            self.tray_manager.set_status(TrayStatus.PROCESSING)
                        # 显示处理中指示器
                        if self.use_floating_indicator and self.processing_indicator:
                            try:
                                self._show_indicator_with_focus_preservation(self.processing_indicator)
                            except Exception as e:
                                logger.warning(f"Failed to show processing indicator: {e}")
                        # 流式传输已在 _stop_recording() 中触发，会自动处理结果
                    except Exception as e:
                        logger.error(f"Error in hotkey release: {e}", exc_info=True)
                        self.log(f"❌ 快捷键释放处理错误: {e}")
                # ==========================================
                # ===================================

                # 处理悬浮指示器事件
                if self.use_floating_indicator:
                    try:
                        if self.floating_indicator and hasattr(self.floating_indicator, 'is_visible') and self.floating_indicator.is_visible:
                            self.floating_indicator.process_events(timeout=0)
                    except Exception as e:
                        logger.warning(f"Floating indicator error: {e}")
                    try:
                        if self.processing_indicator and hasattr(self.processing_indicator, 'is_visible') and self.processing_indicator.is_visible:
                            self.processing_indicator.process_events(timeout=0)
                    except Exception as e:
                        logger.warning(f"Processing indicator error: {e}")

            except Exception as e:
                import traceback
                logger.error(f"UI 循环错误: {e}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                # Log to GUI as well
                self.log(f"❌ GUI 错误: {e}")
                # Don't exit, continue running to allow recovery
                continue

        # 清理资源
        self._cleanup()

    def _cleanup(self):
        """清理资源"""
        try:
            self.is_running = False
            
            # 保存配置
            try:
                if hasattr(self, 'config_manager'):
                    self.config_manager.save()
                    logger.info("配置已保存")
            except Exception as e:
                logger.warning(f"Error saving config: {e}")

            if self.is_recording:
                try:
                    self._stop_recording()
                except Exception as e:
                    logger.warning(f"Error stopping recording: {e}")

            # 停止快捷键监听器
            try:
                if self.hotkey_manager:
                    self.hotkey_manager.stop_listener()
            except Exception as e:
                logger.warning(f"Error stopping hotkey listener: {e}")

            # 停止托盘
            try:
                if self.tray_manager:
                    self.tray_manager.stop()
            except Exception as e:
                logger.warning(f"Error stopping tray manager: {e}")

            # 隐藏指示器
            try:
                if self.floating_indicator:
                    self.floating_indicator.hide()
            except Exception as e:
                logger.warning(f"Error hiding floating indicator: {e}")
            
            try:
                if self.processing_indicator:
                    self.processing_indicator.hide()
            except Exception as e:
                logger.warning(f"Error hiding processing indicator: {e}")

            # 关闭窗口
            try:
                if self.window:
                    self.window.close()
            except Exception as e:
                logger.warning(f"Error closing window: {e}")

            logger.info("Cleanup completed")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)

        if self.ws and self.async_loop:
            asyncio.run_coroutine_threadsafe(self.ws.close(), self.async_loop)

        if self.async_loop:
            self.async_loop.call_soon_threadsafe(self.async_loop.stop)

        if self.window:
            self.window.close()


def main():
    """主程序入口"""
    try:
        import sounddevice
        import websockets
        import httpx
        import pyautogui
        import pynput
    except ImportError as e:
        print(f"缺少依赖: {e}")
        print("\n请运行以下命令安装依赖:")
        print("pip install websockets sounddevice httpx pyautogui pyperclip pynput PySimpleGUI pystray Pillow")
        return

    # 配置管理器会自动处理默认值
    # 但仍支持环境变量覆盖
    host = None
    port = None

    import os
    if "VIF_SERVER_HOST" in os.environ:
        host = os.getenv("VIF_SERVER_HOST")
    if "VIF_SERVER_PORT" in os.environ:
        try:
            port = int(os.getenv("VIF_SERVER_PORT"))
        except ValueError:
            pass

    # 创建客户端并运行（None 值会从配置文件读取）
    client = HotkeyVoiceInputV2(server_host=host, server_port=port)

    try:
        client.run()
    except KeyboardInterrupt:
        print("\n正在退出...")
    except Exception as e:
        print(f"错误: {e}")
        logger.exception("Unhandled exception")


if __name__ == "__main__":
    main()