#!/usr/bin/env python3
"""
Voice Input Framework - Windows GUI Client

A simple and reliable voice input application for Windows.

Requirements:
    pip install PySimpleGUI sounddevice websockets pyperclip

Run:
    python -m client.gui
"""

import asyncio
import base64
import json
import logging
import os
import sys
import threading
from datetime import datetime
from typing import Optional

import PySimpleGUI as sg

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class VoiceInputClient:
    """Voice Input GUI Client"""
    
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
        """Setup the user interface"""
        sg.theme("DarkBlue13")
        
        layout = [
            # Title
            [sg.Text("🎤 Voice Input Framework", 
                    font=("Helvetica", 16, "bold"), 
                    justification="center", 
                    expand_x=True)],
            [sg.HorizontalSeparator()],
            
            # Server Configuration
            [sg.Frame("Server Configuration", [
                [sg.Text("Host:"), 
                 sg.Input(self.server_host, key="-HOST-", size=(20, 1), enable_events=True),
                 sg.Text("Port:"), 
                 sg.Input(str(self.server_port), key="-PORT-", size=(8, 1), enable_events=True),
                 sg.Button("Connect", key="-CONNECT-", button_color=("white", "green"), 
                         size=(10, 1), bind_return_key=True),
                 sg.Text("", key="-STATUS-", size=(15, 1), text_color="yellow")]
            ]),
            
            [sg.HorizontalSeparator()],
            
            # Recording Controls
            [sg.Frame("Recording", [
                [sg.Column([
                    [sg.Button("🎙️  Start Recording", key="-RECORD-", 
                              button_color=("white", "red"), size=(15, 2),
                              font=("Helvetica", 11), disabled=True)],
                    [sg.Button("⏹️  Stop", key="-STOP-", 
                              button_color=("white", "gray"), size=(15, 2),
                              font=("Helvetica", 11), disabled=True)]
                ], element_justification="center")],
                [sg.Text("", key="-RECORD_STATUS-", size=(40, 1), 
                       text_color="cyan", justification="center")]
            ]),
            
            # Results
            [sg.Frame("Recognition Result", [
                [sg.Multiline("", key="-RESULT-", size=(55, 8), 
                          font=("Consolas", 11), autoscroll=True, 
                          readonly=True, background_color="#1e1e1e", 
                          text_color="white", rstrip=True)],
                [sg.Button("📋  Copy to Clipboard", key="-COPY-", 
                         button_color=("white", "#2196F3"), size=(18, 1)),
                 sg.Button("🗑️  Clear", key="-CLEAR-", 
                         button_color=("white", "gray"), size=(15, 1))]
            ]),
            
            # Log
            [sg.Frame("Log", [
                [sg.Multiline("", key="-LOG-", size=(65, 4), 
                          font=("Consolas", 8), autoscroll=True, 
                          readonly=True, background_color="#1e1e1e", 
                          text_color="#aaaaaa")]
            ]),
        ]
        
        self.window = sg.Window(
            "Voice Input Framework v1.0",
            layout,
            finalize=True,
            return_keyboard_events=True
        )
        
        # Bind Enter key to stop recording
        self.window.bind("<Return>", "-STOP-")
        
    def log(self, message: str):
        """Add a log message"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.window["-LOG-"].print(f"[{timestamp}] {message}")
        logger.info(message)
        
    def update_status(self, status: str, color: str = "cyan"):
        """Update the recording status"""
        self.window["-RECORD_STATUS-"].update(status, text_color=color)
        
    async def _connect(self) -> bool:
        """Connect to the WebSocket server"""
        try:
            import websockets
            
            self.server_host = self.window["-HOST-"].get()
            self.server_port = self.window["-PORT-"].get()
            self.server_url = f"ws://{self.server_host}:{self.server_port}/ws/stream"
            
            self.log(f"Connecting to {self.server_url}...")
            
            async with websockets.connect(self.server_url, close_timeout=10) as ws:
                self.ws = ws
                
                # Wait for ready
                resp = await asyncio.wait_for(ws.recv(), timeout=10.0)
                data = json.loads(resp)
                
                if data.get("type") == "ready":
                    self.is_connected = True
                    model = data.get("model", "unknown")
                    self.log(f"✓ Connected! Model: {model}")
                    self.window["-STATUS-"].update("Connected", text_color="green")
                    self.window["-RECORD-"].update(disabled=False)
                    return True
                else:
                    self.log(f"✗ Unexpected response: {data}")
                    return False
                    
        except asyncio.TimeoutError:
            self.log("✗ Connection timeout")
            self.window["-STATUS-"].update("Timeout", text_color="red")
            return False
        except Exception as e:
            self.log(f"✗ Connection failed: {e}")
            self.window["-STATUS-"].update("Failed", text_color="red")
            return False
            
    async def _send_audio(self):
        """Send audio data to server and receive result"""
        try:
            import websockets
            
            if not self.audio_buffer:
                self.log("No audio data")
                return
                
            full_audio = b"".join(self.audio_buffer)
            self.log(f"Sending {len(full_audio)} bytes...")
            
            # Send audio
            await self.ws.send(json.dumps({
                "type": "audio",
                "data": base64.b64encode(full_audio).decode()
            }))
            
            # Send end
            await self.ws.send(json.dumps({"type": "end"}))
            
            # Receive results
            while True:
                resp = await asyncio.wait_for(self.ws.recv(), timeout=60.0)
                data = json.loads(resp)
                
                msg_type = data.get("type")
                
                if msg_type == "result":
                    text = data.get("text", "")
                    if text:
                        self.last_result = text
                        self.window["-RESULT-"].print(text)
                        self.log(f"Result: {text}")
                        
                elif msg_type == "done":
                    self.log("Recognition complete")
                    break
                    
                elif msg_type == "error":
                    error_msg = data.get("error_message", "Unknown error")
                    self.log(f"✗ Error: {error_msg}")
                    break
                    
        except asyncio.TimeoutError:
            self.log("✗ Recognition timeout")
        except Exception as e:
            self.log(f"✗ Send audio failed: {e}")
            
    def _start_recording(self):
        """Start audio recording"""
        import sounddevice as sd
        
        if self.is_recording:
            return
            
        if not self.is_connected:
            self.log("Please connect to server first")
            return
            
        self.is_recording = True
        self.audio_buffer = []
        
        self.window["-RECORD-"].update(disabled=True)
        self.window["-STOP-"].update(disabled=False)
        self.update_status("🔴 Recording... Press Enter or click Stop", "red")
        self.log("Recording started")
        
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
            self.log(f"✗ Failed to start recording: {e}")
            self.is_recording = False
            self._stop_recording()
            
    def _stop_recording(self):
        """Stop audio recording"""
        if not self.is_recording and self.stream is None:
            return
            
        self.is_recording = False
        self.update_status("⏹️ Stopped", "yellow")
        
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.warning(f"Error stopping stream: {e}")
            self.stream = None
            
        self.window["-RECORD-"].update(disabled=False)
        self.window["-STOP-"].update(disabled=True)
        
        audio_size = len(self.audio_buffer) * 1024 * 2 if self.audio_buffer else 0
        self.log(f"Recording stopped. Audio size: {audio_size} bytes")
        
    def run(self):
        """Main event loop"""
        # Start async loop in background thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        def run_async(coro):
            """Run coroutine in the event loop"""
            try:
                loop.run_until_complete(coro)
            except Exception as e:
                logger.error(f"Async error: {e}")
                
        async_task = None
        
        while True:
            event, values = self.window.read(timeout=50)
            
            if event == sg.WIN_CLOSED:
                break
                
            elif event in ("-CONNECT-", "\r"):
                if async_task is None or async_task.done():
                    self.window["-RECORD-"].update(disabled=True)
                    async_task = threading.Thread(
                        target=run_async, 
                        args=(self._connect(),),
                        daemon=True
                    )
                    async_task.start()
                    
            elif event in ("-RECORD-", "-STOP-"):
                if event == "-RECORD-":
                    self._start_recording()
                else:
                    self._stop_recording()
                    # Send audio after stopping
                    if self.audio_buffer:
                        def send_task():
                            loop.run_until_complete(self._send_audio())
                        threading.Thread(target=send_task, daemon=True).start()
                        
            elif event == "-COPY-":
                if self.last_result:
                    import pyperclip
                    pyperclip.copy(self.last_result)
                    self.log("Copied to clipboard")
                    
            elif event == "-CLEAR-":
                self.last_result = ""
                self.window["-RESULT-"].update("")
                self.log("Cleared")
                
        # Cleanup
        self.is_recording = False
        if self.stream:
            self.stream.close()
        loop.close()
        self.window.close()


def main():
    """Main entry point"""
    print("=" * 50)
    print("Voice Input Framework v1.0")
    print("=" * 50)
    print()
    
    # Check dependencies
    missing = []
    for module in ["websockets", "sounddevice", "pyperclip", "PySimpleGUI"]:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
            
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        print()
        print("Install with:")
        print(f"    pip install {' '.join(missing)}")
        return
        
    # Get server address
    server_host = "localhost"
    server_port = 6543
    
    if len(sys.argv) > 1:
        server_host = sys.argv[1]
    if len(sys.argv) > 2:
        server_port = int(sys.argv[2])
        
    print(f"Server: ws://{server_host}:{server_port}/ws/stream")
    print()
    
    # Run the client
    client = VoiceInputClient(server_host, server_port)
    client.run()


if __name__ == "__main__":
    main()
