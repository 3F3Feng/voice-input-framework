#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "PyQt6>=6.6.0",
#     "pynput>=1.7.6",
#     "pyautogui>=0.9.54",
#     "pyperclip>=1.8.2",
#     "websockets>=12.0",
#     "numpy>=1.26.0",
#     "sounddevice>=0.4.6",
# ]
# ///
"""
Voice Input Framework - PyQt6 GUI 客户端

PyQt6 在 Windows 上比 PySide6 更稳定。

使用方法:
    uv run run_gui_qt.py
"""

import sys
import os
import asyncio
import threading
import json
import logging

# 添加项目路径
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)
sys.path.insert(0, os.path.join(project_dir, "client"))
sys.path.insert(0, os.path.join(project_dir, "shared"))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSystemTrayIcon, QMenu, 
    QLabel, QVBoxLayout, QWidget, QPushButton, QHBoxLayout,
    QFrame, QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QColor, QFont, QPixmap, QPainter, QAction

import pyautogui
import pyperclip

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 尝试导入 pynput
try:
    from pynput import keyboard
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False
    logger.warning("pynput not installed, hotkey disabled")


class StatusWindow(QWidget):
    """悬浮状态窗口"""
    
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(350, 120)
        
        # 设置样式
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(30, 30, 30, 220);
                border-radius: 15px;
            }
            QLabel {
                color: white;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        
        # 状态标签
        self.status_label = QLabel("🎤 准备就绪")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #00AAFF;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        
        # 文本标签
        self.text_label = QLabel("")
        self.text_label.setStyleSheet("font-size: 14px; color: white;")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setWordWrap(True)
        layout.addWidget(self.text_label)
        
        # 居中显示
        self._center_on_screen()
        self.hide()
    
    def _center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = screen.height() - self.height() - 150
        self.move(x, y)
    
    def update_status(self, status: str):
        colors = {
            "idle": "#00AAFF",
            "recording": "#FF4444",
            "processing": "#FFAA00"
        }
        texts = {
            "idle": "🎤 准备就绪",
            "recording": "🔴 正在录音...",
            "processing": "⏳ 正在识别..."
        }
        self.status_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {colors.get(status, 'white')};")
        self.status_label.setText(texts.get(status, status))
        
        if status == "recording":
            self.text_label.setText("")
            self.show()
        elif status == "idle":
            QTimer.singleShot(1500, self.hide)
    
    def update_text(self, text: str):
        self.text_label.setText(text)


class Signals(QObject):
    status_changed = pyqtSignal(str)
    text_updated = pyqtSignal(str)
    error_occurred = pyqtSignal(str)


class VoiceInputApp(QMainWindow):
    """语音输入应用主窗口"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("语音输入法")
        self.setFixedSize(300, 200)
        
        # 状态
        self.is_recording = False
        self.server_url = os.getenv("VIF_SERVER", "ws://localhost:6543/ws/stream")
        self._loop = asyncio.new_event_loop()
        
        # 信号
        self.signals = Signals()
        self.signals.status_changed.connect(self._on_status_changed)
        self.signals.text_updated.connect(self._on_text_updated)
        
        # 创建 UI
        self._create_ui()
        self._create_tray()
        self._setup_hotkey()
        
        # 启动异步循环
        self._loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._loop_thread.start()
        
        # 创建状态窗口
        self.status_window = StatusWindow()
        
        logger.info(f"App initialized. Server: {self.server_url}")
    
    def _create_ui(self):
        """创建主界面"""
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 标题
        title = QLabel("语音输入法")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # 状态
        self.status_label = QLabel("🎤 准备就绪")
        self.status_label.setStyleSheet("font-size: 14px; color: #00AAFF;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        
        # 提示
        hint = QLabel("按 Alt+Space 开始录音")
        hint.setStyleSheet("font-size: 12px; color: gray;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        
        # 按钮
        self.btn = QPushButton("开始录音")
        self.btn.clicked.connect(self.toggle_recording)
        layout.addWidget(self.btn)
    
    def _create_tray(self):
        """创建系统托盘"""
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._create_icon())
        self.tray.setToolTip("语音输入法 (Alt+Space)")
        
        menu = QMenu()
        
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self.show)
        menu.addAction(show_action)
        
        menu.addSeparator()
        
        quit_action = QAction("退出", self)
        quit_action.triggered.connect QApplication.quit
        menu.addAction(quit_action)
        
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        
        # 隐藏主窗口
        self.hide()
    
    def _create_icon(self) -> QIcon:
        """创建托盘图标"""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#00AAFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(8, 8, 48, 48)
        
        # 麦克风图案
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(26, 18, 12, 24, 6, 6)
        
        painter.end()
        return QIcon(pixmap)
    
    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_recording()
    
    def _setup_hotkey(self):
        """设置全局快捷键"""
        if not HAS_PYNPUT:
            return
        
        def on_activate():
            QTimer.singleShot(0, self.toggle_recording)
        
        try:
            self.hotkey_listener = keyboard.GlobalHotKeys({
                "<alt>+ ": on_activate,
            })
            self.hotkey_listener.start()
            logger.info("Hotkey registered: Alt+Space")
        except Exception as e:
            logger.warning(f"Failed to register hotkey: {e}")
    
    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
    
    def toggle_recording(self):
        """切换录音状态"""
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()
    
    def start_recording(self):
        """开始录音"""
        self.is_recording = True
        self.signals.status_changed.emit("recording")
        self.btn.setText("停止录音")
        
        asyncio.run_coroutine_threadsafe(self._recording_task(), self._loop)
    
    def stop_recording(self):
        """停止录音"""
        self.is_recording = False
        self.signals.status_changed.emit("processing")
        self.btn.setText("开始录音")
    
    async def _recording_task(self):
        """录音任务"""
        import websockets
        
        full_text = ""
        
        try:
            async with websockets.connect(self.server_url) as ws:
                # 发送配置
                await ws.send(json.dumps({
                    "type": "config",
                    "language": "auto",
                }))
                
                # 等待就绪
                resp = await ws.recv()
                logger.info(f"Server: {resp}")
                
                # 录音循环
                while self.is_recording:
                    await asyncio.sleep(0.1)
                
                # 发送结束信号
                await ws.send(json.dumps({"type": "end"}))
                
                # 接收结果
                while True:
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        data = json.loads(resp)
                        if data.get("type") == "result":
                            full_text = data.get("text", "")
                            self.signals.text_updated.emit(full_text)
                        elif data.get("type") == "done":
                            break
                    except asyncio.TimeoutError:
                        break
            
            if full_text:
                self._type_text(full_text)
                
        except Exception as e:
            logger.error(f"Error: {e}")
            self.signals.error_occurred.emit(str(e))
        
        finally:
            self.is_recording = False
            self.signals.status_changed.emit("idle")
    
    def _type_text(self, text: str):
        """模拟打字"""
        logger.info(f"Typing: {text}")
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    
    def _on_status_changed(self, status: str):
        colors = {"idle": "#00AAFF", "recording": "#FF4444", "processing": "#FFAA00"}
        texts = {"idle": "🎤 准备就绪", "recording": "🔴 正在录音...", "processing": "⏳ 正在识别..."}
        
        self.status_label.setStyleSheet(f"font-size: 14px; color: {colors.get(status, 'white')};")
        self.status_label.setText(texts.get(status, status))
        
        if status == "recording":
            self.btn.setText("停止录音")
        else:
            self.btn.setText("开始录音")
        
        self.status_window.update_status(status)
    
    def _on_text_updated(self, text: str):
        self.status_window.update_text(text)
    
    def closeEvent(self, event):
        """关闭时最小化到托盘"""
        event.ignore()
        self.hide()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    window = VoiceInputApp()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
