#!/usr/bin/env python3
"""
Voice Input Framework - Windows GUI Client
"""

import asyncio
import base64
import json
import logging
import sys
import threading
from datetime import datetime

import PySimpleGUI as sg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class VoiceInputClient:
    def __init__(self, server_host: str = "localhost", server_port: int = 6543):
        self.server_host = server_host
        self.server_port = server_port
        self.server_url = f"ws://{server_host}:{server_port}/ws/stream"
        self.is_recording = False
        self.is_connected = False
        self.audio_buffer = []
        self.last_result = ""
        self.stream = None
        self.ws = None
        self._setup_ui()

    def _setup_ui(self):
        sg.theme("DarkBlue13")
        
        layout = [
            [sg.Text("Voice Input Framework", font=("Helvetica", 16, "bold"), justification="center", expand_x=True)],
            [sg.HorizontalSeparator()],
            
            # Server config
            [sg.Frame("Server", [
                [sg.Text("Host:"), sg.Input(self.server_host, key="-HOST-", size=(20, 1)),
                 sg.Text("Port:"), sg.Input(str(self.server_port), key="-PORT-", size=(8, 1)),
                 sg.Button("Connect", key="-CONNECT-", button_color=("white", "green")),
                 sg.Text("", key="-STATUS-", text_color="yellow")]
            ])],
            
            [sg.HorizontalSeparator()],
            
            # Recording
            [sg.Frame("Recording", [
                [sg.Button("Start", key="-RECORD-", button_color=("white", "red"), disabled=True),
                 sg.Button("Stop", key="-STOP-", button_color=("white", "gray"), disabled=True)],
                [sg.Text("", key="-REC_STATUS-", text_color="cyan")]
            ])],
            
            # Result
            [sg.Frame("Result", [
                [sg.Multiline("", key="-RESULT-", size=(50, 6), font=("Consolas", 10), 
                          autoscroll=True, readonly=True, background_color="#1e1e1e", text_color="white")],
                [sg.Button("Copy", key="-COPY-", button_color=("white", "blue")),
                 sg.Button("Clear", key="-CLEAR-")]
            ])],
            
            # Log
            [sg.Frame("Log", [
                [sg.Multiline("", key="-LOG-", size=(60, 4), font=("Consolas", 8),
                          autoscroll=True, readonly=True, background_color="#1e1e1e", text_color="#aaaaaa")]
            ])],
        ]
        
        self.window = sg.Window("Voice Input Framework", layout, finalize=True)
        self.window.bind("<Return>", "-STOP-")

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.window["-LOG-"].print(f"[{ts}] {msg}")

    async def connect(self):
        try:
            import websockets
            self.server_host = self.window["-HOST-"].get()
            self.server_port = self.window["-PORT-"].get()
            self.server_url = f"ws://{self.server_host}:{self.server_port}/ws/stream"
            self.log(f"Connecting to {self.server_url}...")
            
            async with websockets.connect(self.server_url, close_timeout=10) as ws:
                self.ws = ws
                resp = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(resp)
                
                if data.get("type") == "ready":
                    self.is_connected = True
                    self.log(f"Connected! Model: {data.get('model')}")
                    self.window["-STATUS-"].update("Connected", text_color="green")
                    self.window["-RECORD-"].update(disabled=False)
                    return True
                else:
                    self.log(f"Unexpected: {data}")
                    return False
        except Exception as e:
            self.log(f"Connection failed: {e}")
            self.window["-STATUS-"].update("Failed", text_color="red")
            return False

    async def send_audio(self):
        try:
            import websockets
            if not self.audio_buffer:
                self.log("No audio data")
                return
                
            audio = b"".join(self.audio_buffer)
            self.log(f"Sending {len(audio)} bytes...")
            
            await self.ws.send(json.dumps({"type": "audio", "data": base64.b64encode(audio).decode()}))
            await self.ws.send(json.dumps({"type": "end"}))
            
            while True:
                resp = await asyncio.wait_for(self.ws.recv(), timeout=60)
                data = json.loads(resp)
                
                if data.get("type") == "result":
                    text = data.get("text", "")
                    if text:
                        self.last_result = text
                        self.window["-RESULT-"].print(text)
                        self.log(f"Result: {text}")
                elif data.get("type") == "done":
                    self.log("Done")
                    break
                elif data.get("type") == "error":
                    self.log(f"Error: {data.get('error_message')}")
                    break
        except asyncio.TimeoutError:
            self.log("Timeout")
        except Exception as e:
            self.log(f"Send error: {e}")

    def start_recording(self):
        import sounddevice as sd
        if self.is_recording:
            return
        if not self.is_connected:
            self.log("Not connected")
            return
            
        self.is_recording = True
        self.audio_buffer = []
        self.window["-RECORD-"].update(disabled=True)
        self.window["-STOP-"].update(disabled=False)
        self.window["-REC_STATUS-"].update("Recording...", text_color="red")
        self.log("Recording started")
        
        def callback(indata, frames, time, status):
            if self.is_recording:
                self.audio_buffer.append(indata.tobytes())
        
        try:
            self.stream = sd.InputStream(samplerate=16000, channels=1, dtype='int16',
                                        blocksize=1024, callback=callback)
            self.stream.start()
        except Exception as e:
            self.log(f"Recording error: {e}")
            self.is_recording = False

    def stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except:
                pass
            self.stream = None
        
        self.window["-RECORD-"].update(disabled=False)
        self.window["-STOP-"].update(disabled=True)
        self.window["-REC_STATUS-"].update("Stopped", text_color="yellow")
        self.log(f"Stopped. {len(self.audio_buffer) * 1024 * 2} bytes")

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        def run_async(coro):
            try:
                loop.run_until_complete(coro)
            except Exception as e:
                logger.error(f"Async error: {e}")
        
        while True:
            event, _ = self.window.read(timeout=50)
            
            if event == sg.WIN_CLOSED:
                break
            elif event in ("-CONNECT-", "\r"):
                threading.Thread(target=run_async, args=(self.connect(),), daemon=True).start()
            elif event == "-RECORD-":
                self.start_recording()
            elif event == "-STOP-":
                self.stop_recording()
                if self.audio_buffer:
                    threading.Thread(target=run_async, args=(self.send_audio(),), daemon=True).start()
            elif event == "-COPY-" and self.last_result:
                import pyperclip
                pyperclip.copy(self.last_result)
                self.log("Copied")
            elif event == "-CLEAR-":
                self.last_result = ""
                self.window["-RESULT-"].update("")
        
        self.is_recording = False
        if self.stream:
            self.stream.close()
        loop.close()
        self.window.close()


def main():
    print("Voice Input Framework v1.0")
    
    # Check deps
    for m in ["websockets", "sounddevice", "pyperclip", "PySimpleGUI"]:
        try:
            __import__(m)
        except ImportError:
            print(f"Missing: {m}. Run: pip install {m}")
            return
    
    host, port = "localhost", 6543
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    
    client = VoiceInputClient(host, port)
    client.run()


if __name__ == "__main__":
    main()
