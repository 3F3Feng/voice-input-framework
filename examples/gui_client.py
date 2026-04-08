#!/usr/bin/env python3
"""
Voice Input Framework - Windows GUI 客户端
"""

import asyncio
import base64
import json
import logging
import threading
import time
from datetime import datetime

import PySimpleGUI as sg

# 配置
SERVER_URL = "ws://localhost:6543/ws/stream"
SERVER_HOST = "localhost"
SERVER_PORT = 6543

# 日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VoiceInputGUI:
    def __init__(self):
        self.is_recording = False
        self.audio_buffer = []
        self.full_text = ""
        self.status = "就绪"
        self.server_url = f"ws://{SERVER_HOST}:{SERVER_PORT}/ws/stream"
        
        # 主题
        sg.theme('DarkBlue13')
        
        # 布局
        layout = [
            [sg.Text("🎤 Voice Input Framework", font=("Helvetica", 16, "bold"), justification="center", expand_x=True)],
            [sg.HorizontalSeparator()],
            
            # 服务器配置
            [sg.Frame("服务器配置", [
                [sg.Text("地址:"), sg.Input(SERVER_HOST, key="-HOST-", size=(15, 1)), 
                 sg.Text("端口:"), sg.Input(str(SERVER_PORT), key="-PORT-", size=(8, 1)),
                 sg.Button("连接", key="-CONNECT-", button_color=("white", "green"), size=(8, 1)),
                 sg.Text("", key="-CONN_STATUS-", size=(15, 1), text_color="yellow")]
            ]),
            
            [sg.HorizontalSeparator()],
            
            # 录音控制
            [sg.Frame("录音", [
                [sg.Button("🎙️ 开始录音", key="-RECORD-", button_color=("white", "red"), 
                          size=(15, 2), font=("Helvetica", 12), disabled=True),
                 sg.Button("⏹️ 停止", key="-STOP-", button_color=("white", "gray"), 
                          size=(15, 2), font=("Helvetica", 12), disabled=True)],
                [sg.Text("", key="-RECORD_STATUS-", size=(40, 1), text_color="cyan")]
            ]),
            
            # 识别结果
            [sg.Frame("识别结果", [
                [sg.Multiline("", key="-RESULT-", size=(50, 8), font=("Helvetica", 11), 
                          autoscroll=True, readonly=True, background_color="#1e1e1e", text_color="white")],
                [sg.Button("📋 复制", key="-COPY-", button_color=("white", "blue"), size=(10, 1)),
                 sg.Button("🗑️ 清空", key="-CLEAR-", button_color=("white", "gray"), size=(10, 1))]
            ]),
            
            # 日志
            [sg.Frame("日志", [
                [sg.Multiline("", key="-LOG-", size=(60, 5), font=("Courier", 8), 
                          autoscroll=True, readonly=True, background_color="#1e1e1e", text_color="#aaaaaa")]
            ]),
        ]
        
        self.window = sg.Window("Voice Input Framework", layout, finalize=True)
        self.stream = None
        self.ws = None
        
    def log(self, message):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.window["-LOG-"].print(f"[{timestamp}] {message}")
        
    def update_status(self, status, color="cyan"):
        """更新状态"""
        self.window["-RECORD_STATUS-"].update(status, text_color=color)
        
    async def connect_ws(self):
        """连接 WebSocket"""
        try:
            import websockets
            self.server_url = f"ws://{self.window['-HOST-'].get()}:{self.window['-PORT-'].get()}/ws/stream"
            self.log(f"正在连接 {self.server_url}...")
            
            self.ws = await websockets.connect(self.server_url)
            
            # 等待就绪
            resp = await self.ws.recv()
            data = json.loads(resp)
            
            if data.get("type") == "ready":
                self.log(f"✓ 服务器就绪 (模型: {data.get('model')})")
                self.window["-CONN_STATUS-"].update("已连接", text_color="green")
                self.window["-RECORD-"].update(disabled=False)
                return True
            else:
                self.log(f"✗ 服务器响应异常: {data}")
                return False
                
        except Exception as e:
            self.log(f"✗ 连接失败: {e}")
            self.window["-CONN_STATUS-"].update("连接失败", text_color="red")
            return False
            
    async def send_audio(self):
        """发送音频并接收结果"""
        try:
            import websockets
            
            if not self.audio_buffer:
                self.log("没有音频数据")
                return
                
            full_audio = b"".join(self.audio_buffer)
            self.log(f"发送 {len(full_audio)} 字节音频...")
            
            # 发送音频
            await self.ws.send(json.dumps({
                "type": "audio",
                "data": base64.b64encode(full_audio).decode()
            }))
            
            # 发送结束
            await self.ws.send(json.dumps({"type": "end"}))
            
            # 接收结果
            while True:
                resp = await asyncio.wait_for(self.ws.recv(), timeout=60.0)
                data = json.loads(resp)
                
                msg_type = data.get("type")
                
                if msg_type == "result":
                    text = data.get("text", "")
                    if text:
                        self.full_text = text
                        self.window["-RESULT-"].print(text)
                        self.log(f"识别结果: {text}")
                        
                elif msg_type == "done":
                    self.log("识别完成")
                    break
                    
                elif msg_type == "error":
                    self.log(f"错误: {data.get('error_message')}")
                    break
                    
        except asyncio.TimeoutError:
            self.log("识别超时")
        except Exception as e:
            self.log(f"发送音频失败: {e}")
            
    def start_recording(self):
        """开始录音"""
        import sounddevice as sd
        
        if self.is_recording:
            return
            
        self.is_recording = True
        self.audio_buffer = []
        self.window["-RECORD-"].update(disabled=True)
        self.window["-STOP-"].update(disabled=False)
        self.update_status("🔴 正在录音... 按 Enter 或点击停止", "red")
        self.log("开始录音")
        
        def callback(indata, frames, time, status):
            if status:
                logger.warning(f"Audio status: {status}")
            if self.is_recording:
                self.audio_buffer.append(indata.tobytes())
        
        try:
            self.stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype='int16',
                blocksize=1024,
                callback=callback
            )
            self.stream.start()
        except Exception as e:
            self.log(f"启动录音失败: {e}")
            self.is_recording = False
            
    def stop_recording(self):
        """停止录音"""
        if not self.is_recording:
            return
            
        self.is_recording = False
        self.update_status("⏹️ 停止录音", "yellow")
        
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except:
                pass
            self.stream = None
            
        self.window["-RECORD-"].update(disabled=False)
        self.window["-STOP-"].update(disabled=True)
        self.log(f"停止录音，已采集 {len(self.audio_buffer)} 字节")
        
    def run(self):
        """主循环"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        ws_thread = None
        
        while True:
            event, values = self.window.read(timeout=100)
            
            if event == sg.WIN_CLOSED:
                break
                
            elif event == "-CONNECT-":
                if ws_thread and ws_thread.is_alive():
                    self.log("已经连接")
                    continue
                    
                def connect_task():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.connect_ws())
                    
                ws_thread = threading.Thread(target=connect_task, daemon=True)
                ws_thread.start()
                
            elif event == "-RECORD-":
                self.start_recording()
                
            elif event == "-STOP-":
                self.stop_recording()
                
            elif event == "-COPY-":
                if self.full_text:
                    import pyperclip
                    pyperclip.copy(self.full_text)
                    self.log("已复制到剪贴板")
                    
            elif event == "-CLEAR-":
                self.full_text = ""
                self.window["-RESULT-"].update("")
                self.log("已清空")
                
        # 清理
        self.is_recording = False
        if self.stream:
            self.stream.close()
        if self.ws:
            loop.run_until_complete(self.ws.close())
        loop.close()
        self.window.close()


def main():
    # 检查依赖
    try:
        import websockets
        import sounddevice
        import pyperclip
    except ImportError as e:
        print(f"缺少依赖: {e}")
        print("请运行: pip install websockets sounddevice pyperclip PySimpleGUI")
        return
        
    app = VoiceInputGUI()
    app.run()


if __name__ == "__main__":
    main()
