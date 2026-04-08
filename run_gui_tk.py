#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Voice Input Framework - 轻量级 GUI 客户端 (Tkinter 版本)

使用 Python 自带的 tkinter，无需额外 GUI 依赖。

使用方法:
    uv run run_gui_tk.py
    
或者:
    python run_gui_tk.py
"""

import sys
import os
import asyncio
import threading
import json
import base64
import logging

# 添加项目路径
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)
sys.path.insert(0, os.path.join(project_dir, "client"))
sys.path.insert(0, os.path.join(project_dir, "shared"))

import tkinter as tk
from tkinter import ttk
import pyautogui
import pyperclip

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 尝试导入 pynput，用于全局快捷键
try:
    from pynput import keyboard
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False
    logger.warning("pynput not installed, hotkey disabled")


class VoiceInputApp:
    """语音输入应用"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("语音输入法")
        self.root.geometry("300x150")
        self.root.resizable(False, False)
        
        # 隐藏主窗口，只显示托盘
        self.root.withdraw()
        
        # 状态变量
        self.is_recording = False
        self.server_url = os.getenv("VIF_SERVER", "ws://localhost:6543/ws/stream")
        self._loop = asyncio.new_event_loop()
        
        # 创建 UI
        self._create_ui()
        self._create_tray()
        self._setup_hotkey()
        
        # 启动异步循环
        self._loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._loop_thread.start()
    
    def _create_ui(self):
        """创建状态窗口"""
        # 主框架
        frame = ttk.Frame(self.root, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        # 状态标签
        self.status_var = tk.StringVar(value="🎤 准备就绪")
        self.status_label = ttk.Label(
            frame, 
            textvariable=self.status_var,
            font=("Arial", 14),
            anchor="center"
        )
        self.status_label.pack(pady=10)
        
        # 文本显示
        self.text_var = tk.StringVar(value="")
        self.text_label = ttk.Label(
            frame,
            textvariable=self.text_var,
            font=("Arial", 11),
            wraplength=250,
            anchor="center"
        )
        self.text_label.pack(pady=5)
        
        # 按钮
        self.btn = ttk.Button(frame, text="开始录音 (Alt+Space)", command=self.toggle_recording)
        self.btn.pack(pady=10)
    
    def _create_tray(self):
        """创建系统托盘（简化版）"""
        # 创建托盘窗口（右下角）
        self.tray_window = tk.Toplevel(self.root)
        self.tray_window.overrideredirect(True)
        self.tray_window.attributes("-topmost", True)
        
        # 获取屏幕尺寸，定位到右下角
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.tray_window.geometry(f"150x40+{screen_width-160}+{screen_height-80}")
        
        # 托盘按钮
        tray_btn = tk.Button(
            self.tray_window,
            text="🎤 语音输入",
            command=self.show_window,
            bg="#333",
            fg="white",
            font=("Arial", 10),
            relief=tk.FLAT
        )
        tray_btn.pack(fill=tk.BOTH, expand=True)
        
        # 右键菜单
        self.menu = tk.Menu(self.tray_window, tearoff=0)
        self.menu.add_command(label="显示窗口", command=self.show_window)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)
        
        # 绑定右键
        tray_btn.bind("<Button-3>", lambda e: self.menu.post(e.x_root, e.y_root))
    
    def _setup_hotkey(self):
        """设置全局快捷键"""
        if HAS_PYNPUT:
            def on_activate():
                self.root.after(0, self.toggle_recording)
            
            try:
                self.hotkey_listener = keyboard.GlobalHotKeys({
                    "<alt>+ ": on_activate,  # Alt+Space
                })
                self.hotkey_listener.start()
                logger.info("Hotkey registered: Alt+Space")
            except Exception as e:
                logger.warning(f"Failed to register hotkey: {e}")
    
    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
    
    def show_window(self):
        """显示主窗口"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def toggle_recording(self):
        """切换录音状态"""
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()
    
    def start_recording(self):
        """开始录音"""
        self.is_recording = True
        self.status_var.set("🔴 正在录音...")
        self.btn.config(text="停止录音")
        self.root.deiconify()
        
        asyncio.run_coroutine_threadsafe(self._recording_task(), self._loop)
    
    def stop_recording(self):
        """停止录音"""
        self.is_recording = False
        self.status_var.set("⏳ 正在识别...")
        self.btn.config(text="开始录音")
    
    async def _recording_task(self):
        """录音任务"""
        import websockets
        
        full_text = ""
        
        try:
            # 连接服务器
            async with websockets.connect(self.server_url) as ws:
                # 发送配置
                await ws.send(json.dumps({
                    "type": "config",
                    "language": "auto",
                }))
                
                # 等待就绪
                resp = await ws.recv()
                logger.info(f"Server response: {resp}")
                
                # 模拟录音（这里需要实际的音频采集）
                # 简化版：只接收服务器返回
                seq = 0
                while self.is_recording:
                    # 发送空音频块（占位）
                    # 实际应用中需要采集真实音频
                    await asyncio.sleep(0.1)
                    seq += 1
                
                # 发送结束信号
                await ws.send(json.dumps({"type": "end"}))
                
                # 接收结果
                while True:
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        data = json.loads(resp)
                        if data.get("type") == "result":
                            full_text = data.get("text", "")
                            self.root.after(0, lambda t=full_text: self.text_var.set(t))
                        elif data.get("type") == "done":
                            break
                    except asyncio.TimeoutError:
                        break
            
            if full_text:
                self._type_text(full_text)
                
        except Exception as e:
            logger.error(f"Recording error: {e}")
            self.root.after(0, lambda: self.status_var.set(f"错误: {str(e)[:30]}"))
        
        finally:
            self.is_recording = False
            self.root.after(0, lambda: self.status_var.set("🎤 准备就绪"))
            self.root.after(0, lambda: self.btn.config(text="开始录音"))
    
    def _type_text(self, text: str):
        """模拟打字"""
        logger.info(f"Typing: {text}")
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    
    def quit(self):
        """退出应用"""
        if hasattr(self, 'hotkey_listener'):
            self.hotkey_listener.stop()
        self.root.quit()
    
    def run(self):
        """运行应用"""
        self.root.mainloop()


def main():
    app = VoiceInputApp()
    app.run()


if __name__ == "__main__":
    main()
