#!/usr/bin/env python3
"""
Voice Input Framework - 悬浮录音指示器
提供小型、半透明、可拖动的录音状态指示器

功能:
- 录音时悬浮显示
- 半透明效果
- 可拖动定位
- 自动消失
- 显示录音时长
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Thread safety lock for window operations
_window_lock = threading.Lock()

# 尝试导入 PySimpleGUI
try:
    import PySimpleGUI as sg
    PYSIMPLEGUI_AVAILABLE = True
except ImportError:
    PYSIMPLEGUI_AVAILABLE = False
    logger.warning("PySimpleGUI 未安装,悬浮指示器不可用")


class FloatingIndicator:
    """
    悬浮录音指示器
    小型、半透明、可拖动、自动消失
    """

    def __init__(self,
                 opacity: float = 0.8,
                 size: tuple = (200, 80),
                 auto_hide_delay: float = 0.5):
        """
        初始化悬浮指示器

        Args:
            opacity: 透明度 (0.0-1.0)
            size: 窗口尺寸 (width, height)
            auto_hide_delay: 录音停止后自动隐藏延迟(秒)
        """
        self.opacity = opacity
        self.size = size
        self.auto_hide_delay = auto_hide_delay

        self.window: Optional[sg.Window] = None
        self.is_visible = False
        self.is_recording = False
        
        # Thread safety lock for window operations
        self._window_lock = threading.Lock()

        # 录音计时
        self.recording_start_time: Optional[float] = None
        self.recording_duration = 0.0

        # 更新线程
        self.update_thread: Optional[threading.Thread] = None
        self.stop_update = False

        # 窗口位置(记住用户拖动)
        self.position = None  # (x, y)
        
        # 待处理的计时器更新(线程安全)
        self._pending_timer_update: Optional[str] = None

    def _create_window(self):
        """创建悬浮窗口"""
        if not PYSIMPLEGUI_AVAILABLE:
            return None

        try:
            # 设置主题和样式
            sg.theme("DarkBlue3")

            # 半透明背景色
            bg_color = "#1a1a1a"

            # 布局
            layout = [
                # 录音状态图标和文字
                [sg.Text("🔴", font=("Helvetica", 24), key="-ICON-",
                         background_color=bg_color, justification="center", expand_x=True)],
                [sg.Text("录音中", font=("Helvetica", 12, "bold"), key="-STATUS-",
                         text_color="white", background_color=bg_color,
                         justification="center", expand_x=True)],
                [sg.Text("00:00", font=("Consolas", 16), key="-TIMER-",
                         text_color="#ff6b6b", background_color=bg_color,
                         justification="center", expand_x=True)],
            ]

            # 创建窗口
            window_kwargs = {
                "layout": layout,
                "finalize": True,
                "keep_on_top": True,
                "no_titlebar": True,  # 无标题栏
                "grab_anywhere": True,  # 可拖动
                "alpha_channel": self.opacity,  # 透明度
                "background_color": bg_color,
                "element_justification": "center",
                "margins": (10, 5),
            }

            # 如果有保存的位置，使用它
            if self.position:
                window_kwargs["location"] = self.position

            window = sg.Window("", **window_kwargs)  # title 作为位置参数
            
            return window
        except Exception as e:
            logger.error(f"创建悬浮窗口失败: {e}")
            return None

    def show(self):
        """显示悬浮指示器"""
        if self.is_visible:
            return

        if not PYSIMPLEGUI_AVAILABLE:
            logger.warning("PySimpleGUI 不可用,无法显示悬浮指示器")
            return

        with self._window_lock:
            # 创建窗口
            self.window = self._create_window()
            if not self.window:
                return

            self.is_visible = True
            self.is_recording = True
            self.recording_start_time = time.time()

        # 启动更新线程（线程锁释放后）
        self.stop_update = False
        self.update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self.update_thread.start()

        logger.info("悬浮录音指示器已显示")

    def hide(self):
        """隐藏悬浮指示器"""
        if not self.is_visible:
            return

        self.stop_update = True
        self.is_recording = False
        self.is_visible = False

        if self.update_thread:
            self.update_thread.join(timeout=1.0)
            self.update_thread = None

        with self._window_lock:
            if self.window:
                try:
                    # 记住位置
                    if self.window.TKroot:
                        self.position = self.window.current_location()
                    self.window.close()
                except Exception as e:
                    logger.warning(f"关闭悬浮窗口时出错: {e}")
                finally:
                    self.window = None

        logger.info("悬浮录音指示器已隐藏")

    def set_status(self, status: str, color: str = "white"):
        """
        设置状态文本

        Args:
            status: 状态文本
            color: 文本颜色
        """
        if self.window and self.is_visible:
            try:
                self.window["-STATUS-"].update(status, text_color=color)
            except:
                pass

    def set_icon(self, icon: str):
        """
        设置状态图标

        Args:
            icon: 图标(emoji)
        """
        if self.window and self.is_visible:
            try:
                self.window["-ICON-"].update(icon)
            except:
                pass

    def _update_loop(self):
        """更新循环(在后台线程中运行)"""
        last_duration = -1

        while not self.stop_update and self.is_visible:
            try:
                # 计算录音时长
                if self.recording_start_time and self.is_recording:
                    self.recording_duration = time.time() - self.recording_start_time
                    duration_int = int(self.recording_duration)

                    # 只在秒数变化时更新
                    if duration_int != last_duration:
                        last_duration = duration_int
                        minutes = duration_int // 60
                        seconds = duration_int % 60
                        time_str = f"{minutes:02d}:{seconds:02d}"

                        # 将更新值存储在成员变量中，让主线程读取
                        # 不直接调用 window.write_event_value() 以避免线程冲突
                        self._pending_timer_update = time_str

                time.sleep(0.1)  # 100ms 更新间隔

            except Exception as e:
                logger.error(f"更新录音时长时出错: {e}")
                break

    def process_events(self, timeout: int = 0):
        """
        处理窗口事件(需要在主线程中定期调用)

        Args:
            timeout: 超时时间(毫秒)
        """
        if not self.window or not self.is_visible:
            return

        with self._window_lock:
            if not self.window or not self.is_visible:
                return
                
            try:
                event, values = self.window.read(timeout=max(0, min(100, timeout)))

                if event == sg.WIN_CLOSED or event == sg.TIMEOUT_EVENT:
                    if event == sg.WIN_CLOSED:
                        self.window = None
                        self.is_visible = False
                
                # 检查后台线程是否有待处理的计时器更新
                if self._pending_timer_update and self.window:
                    try:
                        self.window["-TIMER-"].update(self._pending_timer_update)
                        self._pending_timer_update = None  # 清除待处理更新
                    except Exception as e:
                        logger.debug(f"计时器更新失败: {e}")

            except Exception as e:
                logger.debug(f"处理悬浮窗口事件时出错: {e}")

    def pulse(self):
        """
        脉冲效果(录音中状态)
        让图标闪烁以表示正在录音
        """
        if not self.window or not self.is_visible:
            return

        # 在处理中状态下显示不同图标
        current_time = time.time()
        if int(current_time * 2) % 2 == 0:  # 每 0.5 秒切换
            self.window["-ICON-"].update("🔴")
        else:
            self.window["-ICON-"].update("⏺️")


class ProcessingIndicator:
    """
    处理中指示器
    显示语音识别正在进行的动画
    """

    def __init__(self, opacity: float = 0.8, size: tuple = (200, 60)):
        """
        初始化处理中指示器

        Args:
            opacity: 透明度
            size: 窗口尺寸
        """
        self.opacity = opacity
        self.size = size
        self.window: Optional[sg.Window] = None
        self.is_visible = False
        self.animation_frame = 0
        self.animation_thread: Optional[threading.Thread] = None
        self.stop_animation = False
        
        # Thread safety lock for window operations
        self._window_lock = threading.Lock()
        
        # 待处理的图标更新(线程安全)
        self._pending_icon_update: Optional[str] = None

    def _create_window(self) -> Optional[sg.Window]:
        """创建处理中窗口"""
        if not PYSIMPLEGUI_AVAILABLE:
            return None

        try:
            sg.theme("DarkBlue3")
            bg_color = "#1a1a1a"

            layout = [
                [sg.Text("⏳", font=("Helvetica", 20), key="-ICON-",
                         background_color=bg_color, justification="center", expand_x=True)],
                [sg.Text("处理中...", font=("Helvetica", 11), key="-STATUS-",
                         text_color="#4ecdc4", background_color=bg_color,
                         justification="center", expand_x=True)],
            ]

            window = sg.Window(
                "",  # title 为空字符串
                layout,
                finalize=True,
                keep_on_top=True,
                no_titlebar=True,
                grab_anywhere=True,
                alpha_channel=self.opacity,
                background_color=bg_color,
                element_justification="center",
                margins=(10, 5),
            )

            return window
        except Exception as e:
            logger.error(f"创建处理中窗口失败: {e}")
            return None

    def show(self):
        """显示处理中指示器"""
        if self.is_visible:
            return

        with self._window_lock:
            self.window = self._create_window()
            if not self.window:
                return

            self.is_visible = True

        # 启动动画线程（线程锁释放后）
        self.stop_animation = False
        self.animation_thread = threading.Thread(target=self._animation_loop, daemon=True)
        self.animation_thread.start()

        logger.info("处理中指示器已显示")

    def hide(self):
        """隐藏处理中指示器"""
        if not self.is_visible:
            return

        self.stop_animation = True
        self.is_visible = False

        if self.animation_thread:
            self.animation_thread.join(timeout=1.0)
            self.animation_thread = None

        with self._window_lock:
            if self.window:
                try:
                    self.window.close()
                except Exception as e:
                    logger.warning(f"关闭处理中窗口时出错: {e}")
                finally:
                    self.window = None

        logger.info("处理中指示器已隐藏")

    def _animation_loop(self):
        """动画循环"""
        icons = ["⏳", "⌛", "⏳", "⌛"]

        while not self.stop_animation and self.is_visible:
            try:
                if self.window:
                    icon = icons[self.animation_frame % len(icons)]
                    # 将更新值存储在成员变量中，让主线程读取
                    # 不直接调用 window.write_event_value() 以避免线程冲突
                    self._pending_icon_update = icon
                    self.animation_frame += 1
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"动画循环出错: {e}")
                break

    def process_events(self, timeout: int = 0):
        """处理窗口事件"""
        if not self.window or not self.is_visible:
            return

        with self._window_lock:
            if not self.window or not self.is_visible:
                return
                
            try:
                event, values = self.window.read(timeout=max(0, min(100, timeout)))

                if event == sg.WIN_CLOSED or event == sg.TIMEOUT_EVENT:
                    if event == sg.WIN_CLOSED:
                        self.window = None
                        self.is_visible = False
                
                # 检查后台线程是否有待处理的图标更新
                if self._pending_icon_update and self.window:
                    try:
                        self.window["-ICON-"].update(self._pending_icon_update)
                        self._pending_icon_update = None  # 清除待处理更新
                    except Exception as e:
                        logger.debug(f"图标更新失败: {e}")

            except Exception as e:
                logger.debug(f"处理事件出错: {e}")


# 导出
if PYSIMPLEGUI_AVAILABLE:
    __all__ = ['FloatingIndicator', 'ProcessingIndicator']
else:
    __all__ = []
