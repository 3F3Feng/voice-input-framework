"""
Voice Input Framework - 跨平台 GUI 客户端
支持全局快捷键、悬浮窗显示和自动上屏。
"""

import sys
import os
import asyncio
import threading
import time
import queue
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QSystemTrayIcon, QMenu, 
    QLabel, QVBoxLayout, QWidget, QFrame
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer, Slot
from PySide6.QtGui import QIcon, QColor, QFont, QAction, QPixmap, QPainter

from pynput import keyboard
from pynput.keyboard import Controller, Key
import pyautogui
import pyperclip

from voice_input_framework.client.audio_capture import AudioCapturer, AudioConfig
from voice_input_framework.client.stt_client import StreamingSTTClient

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_icon(color: str = "#00AAFF", size: int = 64) -> QIcon:
    """创建一个简单的圆形图标"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(4, 4, size - 8, size - 8)
    
    # 绘制麦克风形状
    painter.setBrush(QColor("white"))
    mic_width = size // 4
    mic_height = size // 2
    painter.drawRoundedRect(
        (size - mic_width) // 2, size // 6,
        mic_width, mic_height // 2,
        mic_width // 2, mic_width // 2
    )
    
    # 麦克风底部
    painter.setPen(QColor("white"))
    painter.setBrush(Qt.NoBrush)
    pen_width = 2
    painter.drawArc(
        (size - mic_width * 2) // 2, size // 3,
        mic_width * 2, size // 2,
        180 * 16, -180 * 16
    )
    
    # 底部竖线
    painter.drawLine(size // 2, size // 2 + size // 6, size // 2, size - 8)
    
    painter.end()
    return QIcon(pixmap)

class STTSignals(QObject):
    """STT 信号定义"""
    status_changed = Signal(str)      # 状态变化: "idle", "recording", "processing"
    transcription_updated = Signal(str, bool)  # (text, is_final)
    error_occurred = Signal(str)

class TranscriptionWindow(QWidget):
    """悬浮识别窗口"""
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | 
            Qt.FramelessWindowHint | 
            Qt.Tool |
            Qt.WindowTransparentForInput  # 鼠标点击穿透
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background-color: rgba(30, 30, 30, 200); border-radius: 10px;")
        
        layout = QVBoxLayout(self)
        
        self.status_label = QLabel("准备就绪")
        self.status_label.setStyleSheet("color: #00AAFF; font-weight: bold;")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
        self.text_label = QLabel("")
        self.text_label.setStyleSheet("color: white; font-size: 16px;")
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.text_label)
        
        self.setFixedSize(400, 150)
        self.hide()
        self._center_on_screen()

    def _center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = screen.height() - self.height() - 100
        self.move(x, y)

    @Slot(str)
    def update_status(self, status: str):
        colors = {
            "idle": "#00AAFF",
            "recording": "#FF4444",
            "processing": "#FFAA00"
        }
        text = {
            "idle": "准备就绪",
            "recording": "正在录音...",
            "processing": "正在识别..."
        }
        self.status_label.setStyleSheet(f"color: {colors.get(status, 'white')}; font-weight: bold;")
        self.status_label.setText(text.get(status, status))
        if status == "recording":
            self.show()
            self.text_label.setText("")

    @Slot(str, bool)
    def update_text(self, text: str, is_final: bool):
        self.text_label.setText(text)
        if is_final:
            QTimer.singleShot(1500, self.hide)

class GUIClientApp(QObject):
    """GUI 客户端主程序"""
    def __init__(self, qt_app: QApplication):
        super().__init__()
        self.qt_app = qt_app
        self.signals = STTSignals()
        self.keyboard_controller = Controller()
        
        # 配置
        self.server_url = os.getenv("VIF_SERVER", "ws://localhost:6543/ws/stream")
        self.hotkey = os.getenv("VIF_HOTKEY", "<alt>+ ")  # 默认 Alt + Space
        if sys.platform == "darwin":
            self.hotkey = os.getenv("VIF_HOTKEY", "<cmd>+ ") # macOS 默认 Cmd + Space
            
        # 状态
        self.is_recording = False
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._loop_thread.start()
        
        # UI 组件
        self.window = TranscriptionWindow()
        self._setup_tray()
        self._setup_hotkey()
        
        # 连接信号
        self.signals.status_changed.connect(self.window.update_status)
        self.signals.status_changed.connect(self.update_tray_status)
        self.signals.transcription_updated.connect(self.window.update_text)
        
        logger.info(f"GUI Client initialized. Hotkey: {self.hotkey}")

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(create_icon("#00AAFF"))
        
        menu = QMenu()
        self.status_action = menu.addAction("🎤 状态: 就绪")
        self.status_action.setEnabled(False)
        menu.addSeparator()
        
        # 快捷键提示
        hotkey_text = "Alt+Space" if sys.platform != "darwin" else "Cmd+Space"
        hotkey_action = menu.addAction(f"快捷键: {hotkey_text}")
        hotkey_action.setEnabled(False)
        menu.addSeparator()
        
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self.qt_app.quit)
        
        self.tray.setContextMenu(menu)
        self.tray.show()
        self.tray.setToolTip(f"语音输入法 ({hotkey_text} 开始录音)")
        
        # 连接双击事件
        self.tray.activated.connect(self._on_tray_activated)
    
    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            asyncio.run_coroutine_threadsafe(self.toggle_recording(), self._loop)
    
    @Slot(str)
    def update_tray_status(self, status: str):
        icons = {
            "idle": "🎤",
            "recording": "🔴",
            "processing": "⏳"
        }
        self.status_action.setText(f"{icons.get(status, '🎤')} 状态: {status}")

    def _setup_hotkey(self):
        """设置全局快捷键"""
        def on_activate():
            self.qt_app.postEvent(self, QObject()) # 确保在主线程执行切换
            asyncio.run_coroutine_threadsafe(self.toggle_recording(), self._loop)

        self.hotkey_listener = keyboard.GlobalHotKeys({
            self.hotkey: on_activate
        })
        self.hotkey_listener.start()

    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def toggle_recording(self):
        if self.is_recording:
            await self.stop_recording()
        else:
            await self.start_recording()

    async def start_recording(self):
        if self.is_recording: return
        self.is_recording = True
        self.signals.status_changed.emit("recording")
        
        # 异步启动录音和识别
        asyncio.run_coroutine_threadsafe(self._recording_task(), self._loop)

    async def stop_recording(self):
        if not self.is_recording: return
        self.is_recording = False
        self.signals.status_changed.emit("processing")

    async def _recording_task(self):
        try:
            client = StreamingSTTClient(server_url=self.server_url)
            await client.connect()
            
            capturer = AudioCapturer(AudioConfig(sample_rate=16000))
            await capturer.start()
            
            seq = 0
            full_text = ""
            
            async for chunk in capturer.stream():
                if not self.is_recording:
                    break
                await client.send_audio(chunk, seq)
                seq += 1
                
                # 尝试获取中间结果
                result = await client.receive_result(timeout=0.01)
                if result and result.get("type") == "result":
                    text = result.get("text", "")
                    self.signals.transcription_updated.emit(text, False)
            
            # 停止并获取最终结果
            await capturer.stop()
            async for result in client.end_stream():
                if result.get("type") == "result":
                    full_text = result.get("text", "")
                    self.signals.transcription_updated.emit(full_text, True)
            
            await client.close()
            
            if full_text:
                self._type_text(full_text)
                
        except Exception as e:
            logger.error(f"Recording task error: {e}")
            self.signals.error_occurred.emit(str(e))
        finally:
            self.is_recording = False
            self.signals.status_changed.emit("idle")

    def _type_text(self, text: str):
        """模拟打字输入文字"""
        logger.info(f"Typing text: {text}")
        # 使用剪贴板 + 粘贴的方式处理中文混输更稳定
        pyperclip.copy(text)
        
        if sys.platform == "darwin":
            # macOS: Command + V
            with self.keyboard_controller.pressed(Key.cmd):
                self.keyboard_controller.tap('v')
        else:
            # Windows/Linux: Control + V
            with self.keyboard_controller.pressed(Key.ctrl):
                self.keyboard_controller.tap('v')

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    client = GUIClientApp(app)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
