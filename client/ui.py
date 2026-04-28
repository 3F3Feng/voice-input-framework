#!/usr/bin/env python3
"""
客户端 UI 模块 — PySimpleGUI 布局、窗口管理、系统托盘

从 gui.py 拆分出来的 UI 相关职责：
- 主窗口布局构建
- UI 元素更新（状态、日志、结果、错误）
- 系统托盘菜单管理
- 悬浮指示器管理
"""

import logging
import time
import threading
from datetime import datetime
from typing import Optional, Callable

import PySimpleGUI as sg

from .hotkey_manager import HotkeyPresets
from .tray_manager import TrayIconManager, TrayStatus
from .floating_indicator import FloatingIndicator, ProcessingIndicator
from .auto_start import AutoStartManager
from .update_checker import check_for_updates, format_version_message

logger = logging.getLogger(__name__)

# UI 配色常量
BACKGROUND_COLOR = "#2e2e2e"
TITLE_TEXT_COLOR = "#ffcc66"
GROUP_TEXT_COLOR = "#66ccff"
TEXT_COLOR = "#66ccff"
TIP_TEXT_COLOR = "#cccccc"
BUTTON_COLOR = ("white", "gray")


# 焦点管理函数（Windows）
try:
    import win32gui
    import win32con
    WINAPI_AVAILABLE = True
except ImportError:
    WINAPI_AVAILABLE = False


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
    """获取合适的光标位置显示浮标"""
    try:
        import pyautogui
        x, y = pyautogui.position()
        return (x, y - 20)
    except Exception as e:
        logger.debug(f"获取光标位置失败: {e}")
        return None


class MainWindow:
    """主窗口 UI — 负责布局构建和 UI 元素更新"""

    def __init__(self, config_manager, audio_devices: dict,
                 server_host: str, server_port: int):
        self.config_manager = config_manager
        self.audio_devices = audio_devices
        self.server_host = server_host
        self.server_port = server_port
        self.window: Optional[sg.Window] = None

    def build_layout(self) -> list:
        """构建 PySimpleGUI 布局"""
        # 获取第一个设备名称作为默认值
        default_device_name = (
            list(self.audio_devices.values())[0]
            if self.audio_devices else "默认设备"
        )

        layout = [
            [sg.Text("🎤 Voice Input v1.1", font=("Helvetica", 14, "bold"),
                     justification="center", expand_x=True,
                     background_color=BACKGROUND_COLOR,
                     text_color=TITLE_TEXT_COLOR)],
            [sg.HorizontalSeparator()],

            # 连接状态
            [sg.Text(f"服务器: {self.server_host}:{self.server_port}",
                     size=(50, 1), background_color=BACKGROUND_COLOR,
                     text_color=TEXT_COLOR),
             sg.Text("未连接", key="-STATUS-", text_color="red",
                     size=(15, 1), background_color=BACKGROUND_COLOR)],

            # 快捷键设置
            [sg.Frame("快捷键设置", [
                [sg.Text("开始/停止录音:", background_color=BACKGROUND_COLOR,
                         text_color=TEXT_COLOR),
                 sg.Input(self.config_manager.hotkey, key="-HOTKEY-",
                          size=(30, 1)),
                 sg.Button("录制", key="-RECORD-HOTKEY-", size=(8, 1)),
                 sg.Button("更新", key="-UPDATE-HOTKEY-", size=(8, 1)),
                 sg.Button("清除", key="-CLEAR-HOTKEY-", size=(8, 1))],
                [sg.Checkbox("区分左右修饰键",
                             default=self.config_manager.distinguish_left_right,
                             key="-DISTINGUISH-LR-", enable_events=True,
                             background_color=BACKGROUND_COLOR,
                             text_color=TEXT_COLOR)],
                [sg.Text("(按住快捷键说话，松开后自动输入)",
                         text_color=TIP_TEXT_COLOR, font=("Helvetica", 9),
                         background_color=BACKGROUND_COLOR)],
                [sg.Text("预设方案:", background_color=BACKGROUND_COLOR,
                         text_color=TEXT_COLOR),
                 sg.Combo(list(HotkeyPresets.get_preset_names()),
                          default_value="default", key="-HOTKEY-PRESET-",
                          size=(20, 1), readonly=True, enable_events=True),
                 sg.Button("应用预设", key="-APPLY-PRESET-", size=(10, 1))],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            # 麦克风选择
            [sg.Frame("麦克风设置", [
                [sg.Text("麦克风:", background_color=BACKGROUND_COLOR,
                         text_color=TEXT_COLOR),
                 sg.Combo(list(self.audio_devices.values()),
                          default_value=default_device_name,
                          key="-MICROPHONE-", size=(50, 1), readonly=True)],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            # 服务器配置
            [sg.Frame("服务器配置", [
                [sg.Text("主机:", background_color=BACKGROUND_COLOR,
                         text_color=TEXT_COLOR),
                 sg.Input(self.server_host, key="-HOST-", size=(20, 1)),
                 sg.Text("端口:", background_color=BACKGROUND_COLOR,
                         text_color=TEXT_COLOR),
                 sg.Input(str(self.server_port), key="-PORT-", size=(8, 1))],
                [sg.Button("连接", key="-CONNECT-",
                           button_color=("white", "green"), size=(10, 1)),
                 sg.Text("", key="-CONN-STATUS-", text_color="yellow",
                         background_color=BACKGROUND_COLOR)],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            # 模型选择
            [sg.Frame("模型设置", [
                [sg.Text("STT模型:", background_color=BACKGROUND_COLOR,
                         text_color=TEXT_COLOR),
                 sg.Combo([], default_value="", key="-MODEL-SELECT-",
                          size=(25, 1), readonly=True),
                 sg.Button("刷新", key="-REFRESH-MODELS-", size=(8, 1)),
                 sg.Button("切换", key="-SWITCH-MODEL-",
                           button_color=("white", "blue"), size=(8, 1))],
                [sg.Text("", key="-MODEL-STATUS-", text_color="yellow",
                         size=(70, 1), background_color=BACKGROUND_COLOR)],
                [sg.HorizontalSeparator()],
                [sg.Text("LLM模型:", background_color=BACKGROUND_COLOR,
                         text_color=TEXT_COLOR),
                 sg.Combo([], default_value="", key="-LLM-MODEL-SELECT-",
                          size=(25, 1), readonly=True),
                 sg.Button("刷新", key="-REFRESH-LLM-MODELS-", size=(8, 1)),
                 sg.Button("切换", key="-SWITCH-LLM-MODEL-",
                           button_color=("white", "purple"), size=(8, 1)),
                 sg.Text("", size=(5, 1), background_color=BACKGROUND_COLOR),
                 sg.Checkbox("启用LLM后处理", key="-LLM-ENABLED-",
                             enable_events=True,
                             default=self.config_manager.llm_enabled,
                             text_color=TEXT_COLOR,
                             background_color=BACKGROUND_COLOR,
                             size=(15, 1))],
                [sg.Text("", key="-LLM-MODEL-STATUS-", text_color="cyan",
                         size=(70, 1), background_color=BACKGROUND_COLOR)],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            # LLM 提示词配置
            [sg.HorizontalSeparator()],
            [sg.Frame("LLM 提示词配置", [
                [sg.Multiline("", key="-LLM-PROMPT-", size=(60, 5),
                              font=("Consolas", 9))],
                [sg.Button("加载", key="-LOAD-PROMPT-", size=(8, 1)),
                 sg.Button("保存", key="-SAVE-PROMPT-", size=(8, 1)),
                 sg.Text("", key="-PROMPT-STATUS-", text_color="yellow",
                         size=(30, 1))],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            # 界面设置
            [sg.Frame("界面设置", [
                [sg.Checkbox("启动时最小化到托盘",
                             default=self.config_manager.start_minimized,
                             key="-START-MINIMIZED-", enable_events=True,
                             background_color=BACKGROUND_COLOR,
                             text_color=TEXT_COLOR)],
                [sg.Checkbox("使用悬浮录音指示器",
                             default=self.config_manager.use_floating_indicator,
                             key="-USE-INDICATOR-", enable_events=True,
                             background_color=BACKGROUND_COLOR,
                             text_color=TEXT_COLOR),
                 sg.Button("显示主窗口", key="-SHOW-WINDOW-",
                           size=(15, 1))],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            # 识别结果
            [sg.Frame("识别结果", [
                [sg.Multiline("", key="-RESULT-", size=(80, 8),
                              font=("Consolas", 10), autoscroll=True,
                              disabled=True, background_color="#1e1e1e",
                              text_color="white")],
                [sg.Button("复制", key="-COPY-", size=(10, 1)),
                 sg.Button("清空", key="-CLEAR-", size=(10, 1)),
                 sg.Button("输入（自动）", key="-PASTE-", size=(15, 1))],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            # 日志
            [sg.Frame("日志", [
                [sg.Multiline("", key="-LOG-", size=(80, 5),
                              font=("Consolas", 10), autoscroll=True,
                              disabled=True, background_color="#1e1e1e",
                              text_color="#aaaaaa")],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            # 错误信息
            [sg.Frame("错误信息", [
                [sg.Multiline("", key="-ERROR-", size=(80, 3),
                              font=("Consolas", 10), autoscroll=True,
                              disabled=True, background_color="#3e1e1e",
                              text_color="#ff8888")],
            ], background_color=BACKGROUND_COLOR, title_color=GROUP_TEXT_COLOR,
                expand_x=True)],

            [sg.Push(background_color=BACKGROUND_COLOR),
             sg.Button("退出", key="-EXIT-",
                       button_color=("white", "gray"), size=(10, 1)),
             sg.Button("最小化到托盘", key="-MINIMIZE-TRAY-", size=(15, 1)),
             sg.Push(background_color=BACKGROUND_COLOR)],
        ]
        return layout

    def create_window(self, start_minimized: bool = False) -> sg.Window:
        """创建并返回主窗口"""
        sg.theme("DarkBlue3")
        layout = self.build_layout()

        self.window = sg.Window(
            "🎤 Voice Input Framework v1.1",
            layout,
            finalize=True,
            keep_on_top=True,
            no_titlebar=True,
            grab_anywhere=True,
            background_color="#2e2e2e",
            button_color=("white", "#4e4e4e"),
        )

        return self.window

    # ──────────────────── UI 更新方法 ────────────────────

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

    def show_error(self, message: str):
        """显示错误信息"""
        if not self.window:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.window["-ERROR-"].print(f"[{timestamp}] ❌ {message}")

    def set_status(self, status: str, color: str = "yellow"):
        """更新连接状态"""
        if self.window:
            self.window["-STATUS-"].update(status, text_color=color)

    def update_model_list(self, models: list, current: str):
        """更新 STT 模型下拉列表"""
        if not self.window:
            return
        if models:
            self.window["-MODEL-SELECT-"].update(values=models, value=current)
            self.window["-MODEL-STATUS-"].update(
                f"当前模型: {current}", text_color="yellow"
            )
        else:
            self.window["-MODEL-SELECT-"].update(values=[], value="")
            self.window["-MODEL-STATUS-"].update("未找到可用模型", text_color="red")

    def update_model_status(self, text: str, color: str = "yellow"):
        """更新 STT 模型状态文本"""
        if self.window:
            self.window["-MODEL-STATUS-"].update(text, text_color=color)

    def update_llm_model_list(self, models: list, current: str,
                              enabled: bool = True):
        """更新 LLM 模型下拉列表"""
        if not self.window:
            return
        self.window["-LLM-MODEL-SELECT-"].update(values=models, value=current)
        self.window["-LLM-MODEL-STATUS-"].update(
            f"当前: {current}",
            text_color="cyan" if enabled else "gray",
        )

    def update_llm_model_status(self, text: str, color: str = "cyan"):
        """更新 LLM 模型状态文本"""
        if self.window:
            self.window["-LLM-MODEL-STATUS-"].update(text, text_color=color)

    def update_llm_enabled(self, enabled: bool):
        """更新 LLM 启用复选框"""
        if self.window:
            self.window["-LLM-ENABLED-"].update(enabled)

    def update_prompt(self, text: str):
        """更新提示词文本框"""
        if self.window:
            self.window["-LLM-PROMPT-"].update(text)

    def update_prompt_status(self, text: str, color: str = "yellow"):
        """更新提示词状态"""
        if self.window:
            self.window["-PROMPT-STATUS-"].update(text, text_color=color)

    def update_hotkey_input(self, text: str):
        """更新快捷键输入框"""
        if self.window:
            self.window["-HOTKEY-"].update(text)

    def read(self, timeout: int = 100):
        """读取窗口事件"""
        if not self.window:
            return None, {}
        return self.window.read(timeout=timeout)

    def hide(self):
        """隐藏窗口"""
        if self.window:
            self.window.hide()

    def un_hide(self):
        """取消隐藏窗口"""
        if self.window:
            self.window.un_hide()

    def close(self):
        """关闭窗口"""
        if self.window:
            self.window.close()
            self.window = None

    def write_event_value(self, key, value):
        """线程安全地写入事件"""
        if self.window:
            self.window.write_event_value(key, value)


class TrayMenu:
    """系统托盘菜单 — 管理托盘图标和回调"""

    def __init__(self, tray_manager: TrayIconManager,
                 auto_start_manager: AutoStartManager):
        self.tray_manager = tray_manager
        self.auto_start_manager = auto_start_manager

    def setup(self, callbacks: dict):
        """设置托盘回调

        Args:
            callbacks: 回调函数字典，键包括：
                show_window, hide_window, start_recording, stop_recording,
                switch_model, refresh_models, check_update,
                toggle_auto_start, quit
        """
        for name, callback in callbacks.items():
            self.tray_manager.set_callback(name, callback)

    def set_models(self, available: list, current: str):
        """更新托盘模型信息"""
        self.tray_manager.set_available_models(available)
        self.tray_manager.set_current_model(current)

    def set_current_model(self, model_name: str):
        """更新当前模型名"""
        self.tray_manager.set_current_model(model_name)

    def set_status(self, status: TrayStatus):
        """更新托盘状态"""
        self.tray_manager.set_status(status)

    def set_auto_start(self, enabled: bool):
        """更新自启动状态"""
        self.tray_manager.set_auto_start_enabled(enabled)

    def start(self):
        """启动托盘"""
        self.tray_manager.start()

    def stop(self):
        """停止托盘"""
        self.tray_manager.stop()

    def notify(self, title: str, message: str):
        """显示托盘通知"""
        if self.tray_manager and self.tray_manager.icon:
            try:
                self.tray_manager.notify(title, message)
            except Exception as e:
                logger.warning(f"托盘通知失败: {e}")


class IndicatorManager:
    """悬浮指示器管理"""

    def __init__(self, audio_level_callback: Callable):
        self.floating_indicator = FloatingIndicator(
            follow_mouse=False, audio_callback=audio_level_callback
        )
        self.processing_indicator = ProcessingIndicator(follow_mouse=False)
        self.use_floating_indicator = True

    def show_recording(self, cursor_pos=None):
        """显示录音指示器"""
        if not self.use_floating_indicator:
            return
        focus_hwnd = get_foreground_window()
        if cursor_pos is None:
            cursor_pos = get_input_cursor_position()
        self.floating_indicator.show(cursor_pos=cursor_pos)
        if focus_hwnd:
            restore_focus_later(focus_hwnd, delay_ms=50)

    def show_processing(self, cursor_pos=None):
        """显示处理中指示器"""
        if not self.use_floating_indicator:
            return
        focus_hwnd = get_foreground_window()
        if cursor_pos is None:
            cursor_pos = get_input_cursor_position()
        self.processing_indicator.show(cursor_pos=cursor_pos)
        if focus_hwnd:
            restore_focus_later(focus_hwnd, delay_ms=50)

    def set_processing_status(self, text: str, color: str):
        """更新处理中指示器状态"""
        try:
            self.processing_indicator.set_status(text, color)
        except Exception as e:
            logger.warning(f"更新处理状态失败: {e}")

    def hide_recording(self):
        """隐藏录音指示器"""
        try:
            self.floating_indicator.hide()
        except Exception as e:
            logger.warning(f"隐藏录音指示器失败: {e}")

    def hide_processing(self):
        """隐藏处理中指示器"""
        try:
            self.processing_indicator.hide()
        except Exception as e:
            logger.warning(f"隐藏处理指示器失败: {e}")

    def process_events(self, timeout: int = 0):
        """处理指示器事件"""
        try:
            if (self.floating_indicator
                    and hasattr(self.floating_indicator, 'is_visible')
                    and self.floating_indicator.is_visible):
                self.floating_indicator.process_events(timeout=timeout)
        except Exception as e:
            logger.warning(f"Floating indicator error: {e}")

        try:
            if (self.processing_indicator
                    and hasattr(self.processing_indicator, 'is_visible')
                    and self.processing_indicator.is_visible):
                self.processing_indicator.process_events(timeout=timeout)
        except Exception as e:
            logger.warning(f"Processing indicator error: {e}")

    def cleanup(self):
        """清理指示器"""
        try:
            self.floating_indicator.hide()
        except Exception:
            pass
        try:
            self.processing_indicator.hide()
        except Exception:
            pass
