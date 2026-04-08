#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "rich>=13.7.0",
#     "pynput>=1.7.6",
#     "pyperclip>=1.8.2",
#     "websockets>=12.0",
#     "questionary>=2.0.0",
#     "sounddevice>=0.4.6",
#     "numpy>=1.26.0",
#     "webrtcvad>=2.0.10",
# ]
# ///
"""
Voice Input Framework - 交互式 CLI 客户端

使用 Rich + Questionary 提供美观的交互式界面。
支持麦克风选择、录音和流式识别。
"""

import sys
import os
import asyncio
import json
import logging
import threading
import time
import base64
from pathlib import Path

# 添加项目路径
project_dir = Path(__file__).parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

# 配置文件路径
CONFIG_FILE = Path.home() / ".voice_input_config.json"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_CONFIG = {
    "server_host": "localhost",
    "server_port": 6543,
    "hotkey": "alt+space",
    "language": "auto",
    "auto_paste": True,
    "microphone": "default",
    "sample_rate": 16000,
}

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    print("请安装 rich: pip install rich")
    sys.exit(1)

try:
    from pynput import keyboard
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False
    logger.warning("pynput 未安装，快捷键功能不可用")

import pyperclip


class Config:
    """配置管理"""
    
    def __init__(self):
        self.data = DEFAULT_CONFIG.copy()
        self.load()
    
    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.data.update(json.load(f))
                logger.info(f"配置已加载: {CONFIG_FILE}")
            except Exception as e:
                logger.warning(f"加载配置失败: {e}")
    
    def save(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.data, f, indent=2)
            logger.info(f"配置已保存: {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
    
    def get(self, key, default=None):
        return self.data.get(key, default)
    
    def set(self, key, value):
        self.data[key] = value
        self.save()
    
    @property
    def server_url(self):
        return f"ws://{self.data['server_host']}:{self.data['server_port']}/ws/stream"
    
    @property
    def http_url(self):
        return f"http://{self.data['server_host']}:{self.data['server_port']}"


def list_microphones():
    """列出可用的麦克风"""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        mics = []
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                mics.append({
                    'id': i,
                    'name': dev['name'],
                    'channels': dev['max_input_channels'],
                    'sample_rate': int(dev['default_samplerate']),
                })
        return mics
    except Exception as e:
        logger.error(f"Failed to list microphones: {e}")
        return []


class AudioCapture:
    """简单的音频采集器"""
    
    def __init__(self, device_id=None, sample_rate=16000, channels=1):
        self.device_id = device_id
        self.sample_rate = sample_rate
        self.channels = channels
        self._stream = None
        self._is_recording = False
        self._queue = asyncio.Queue()
    
    async def start(self):
        """开始采集"""
        import sounddevice as sd
        
        self._is_recording = True
        
        def callback(indata, frames, time, status):
            if status:
                logger.warning(f"Audio status: {status}")
            # 只保留最后一个块
            if self._is_recording:
                self._queue.put_nowait(indata.tobytes())
        
        self._stream = sd.InputStream(
            device=self.device_id,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            blocksize=1024,
            callback=callback
        )
        self._stream.start()
        logger.info(f"Audio capture started on device {self.device_id}")
    
    async def stop(self):
        """停止采集"""
        self._is_recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("Audio capture stopped")
    
    async def stream(self):
        """音频流生成器"""
        await self.start()
        try:
            while self._is_recording:
                try:
                    data = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    yield data
                except asyncio.TimeoutError:
                    continue
        finally:
            await self.stop()


class VoiceInputCLI:
    """语音输入 CLI 应用"""
    
    def __init__(self):
        self.console = Console()
        self.config = Config()
        self.is_recording = False
        self._loop = asyncio.new_event_loop()
        self._hotkey_listener = None
        self._running = True
    
    def clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def show_header(self):
        self.console.print(Panel.fit(
            "[bold cyan]🎤 Voice Input Framework[/bold cyan]\n"
            "[dim]交互式语音输入 CLI 客户端[/dim]",
            border_style="cyan"
        ))
    
    def show_status(self):
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="white")
        
        table.add_row("服务器", f"{self.config.get('server_host')}:{self.config.get('server_port')}")
        table.add_row("麦克风", self._get_mic_name())
        table.add_row("快捷键", f"[bold]{self.config.get('hotkey')}[/bold]")
        table.add_row("语言", self.config.get('language'))
        table.add_row("自动粘贴", "✓" if self.config.get('auto_paste') else "✗")
        
        self.console.print(Panel(table, title="当前配置", border_style="blue"))
    
    def _get_mic_name(self):
        mics = list_microphones()
        mic_id = self.config.get('microphone')
        if mic_id == "default" or mic_id is None:
            return "默认麦克风"
        for mic in mics:
            if mic['id'] == mic_id:
                return mic['name']
        return f"麦克风 #{mic_id}"
    
    def show_menu(self):
        self.console.print()
        self.console.print("[bold]操作菜单:[/bold]")
        self.console.print("  [1] 🎤 开始录音")
        self.console.print("  [2] ⚙️  设置")
        self.console.print("  [3] 📋 查看配置")
        self.console.print("  [4] 🔗 测试连接")
        self.console.print("  [5] 🎙️  选择麦克风")
        self.console.print("  [6] ❓ 帮助")
        self.console.print("  [q] 🚪 退出")
        self.console.print()
    
    async def test_connection(self):
        import websockets
        
        self.console.print(f"\n[yellow]正在连接 {self.config.server_url}...[/yellow]")
        
        try:
            async with websockets.connect(self.config.server_url, close_timeout=5) as ws:
                await ws.send(json.dumps({
                    "type": "config",
                    "language": "auto",
                }))
                
                resp = await ws.recv()
                data = json.loads(resp)
                
                if data.get("type") == "ready":
                    self.console.print(f"[green]✓ 连接成功！服务器模型: {data.get('model', 'unknown')}[/green]")
                    return True
                else:
                    self.console.print(f"[red]✗ 服务器响应异常: {data}[/red]")
                    return False
                    
        except Exception as e:
            self.console.print(f"[red]✗ 连接失败: {e}[/red]")
            return False
    
    async def record_and_transcribe(self):
        """录音并识别 - 简化版"""
        import websockets
        import sounddevice as sd
        
        if self.is_recording:
            self.console.print("[yellow]已经在录音中...[/yellow]")
            return
        
        self.is_recording = True
        self.console.print()
        self.console.print(Panel(
            "[bold red]🔴 正在录音...[/bold red]\n"
            "[dim]按 Enter 停止[/dim]",
            border_style="red"
        ))
        
        mic_name = self._get_mic_name()
        self.console.print(f"[dim]使用麦克风: {mic_name}[/dim]")
        
        # 音频缓冲区
        audio_buffer = []
        is_recording_audio = True
        
        def audio_callback(indata, frames, time, status):
            if status:
                logger.warning(f"Audio status: {status}")
            if is_recording_audio:
                audio_buffer.append(indata.tobytes())
        
        try:
            # 启动音频采集
            stream = sd.InputStream(
                device=self.config.get('microphone'),
                samplerate=self.config.get('sample_rate', 16000),
                channels=1,
                dtype='int16',
                blocksize=1024,
                callback=audio_callback
            )
            with stream:
                # 等待用户按 Enter (不阻塞事件循环)
                await asyncio.get_event_loop().run_in_executor(None, input, "")
            
            is_recording_audio = False
            
        except Exception as e:
            self.console.print(f"[red]录音失败: {e}[/red]")
            self.is_recording = False
            return
        
        self.console.print("[dim]正在识别...[/dim]")
        
        # 检查是否有音频
        if not audio_buffer:
            self.console.print("[yellow]未检测到音频[/yellow]")
            self.is_recording = False
            return
        
        full_audio = b"".join(audio_buffer)
        self.console.print(f"[dim]已采集 {len(full_audio)} 字节音频[/dim]")
        
        # 发送到服务器
        full_text = ""
        try:
            async with websockets.connect(self.config.server_url) as ws:
                # 发送配置
                await ws.send(json.dumps({
                    "type": "config",
                    "language": self.config.get('language', 'auto'),
                }))
                
                # 等待就绪
                resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(resp)
                
                if data.get("type") != "ready":
                    self.console.print(f"[red]服务器未就绪: {data}[/red]")
                    self.is_recording = False
                    return
                
                self.console.print("[green]✓ 服务器就绪，发送音频...[/green]")
                
                # 发送音频
                await ws.send(json.dumps({
                    "type": "audio",
                    "data": base64.b64encode(full_audio).decode('utf-8'),
                }))
                
                # 发送结束
                await ws.send(json.dumps({"type": "end"}))
                
                # 接收结果
                while True:
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        data = json.loads(resp)
                        
                        if data.get("type") == "result":
                            full_text = data.get("text", "")
                        elif data.get("type") == "done":
                            break
                        elif data.get("type") == "error":
                            self.console.print(f"[red]识别错误: {data.get('error_message')}[/red]")
                            break
                            
                    except asyncio.TimeoutError:
                        self.console.print("[yellow]识别超时[/yellow]")
                        break
                
        except Exception as e:
            self.console.print(f"[red]识别失败: {e}[/red]")
            logger.error(f"Recognition error: {e}")
        
        self.console.print()
        
        if full_text:
            self.console.print(Panel(
                f"[bold green]{full_text}[/bold green]",
                title="识别结果",
                border_style="green"
            ))
            
            if self.config.get('auto_paste'):
                pyperclip.copy(full_text)
                self.console.print("[dim]已复制到剪贴板[/dim]")
        else:
            self.console.print("[yellow]未检测到语音内容[/yellow]")
        
        self.is_recording = False
    

    def settings_menu(self):
        while True:
            self.clear_screen()
            self.show_header()
            
            self.console.print("\n[bold]⚙️  设置菜单[/bold]\n")
            self.console.print("  [1] 服务器地址")
            self.console.print("  [2] 服务器端口")
            self.console.print("  [3] 快捷键")
            self.console.print("  [4] 语言设置")
            self.console.print("  [5] 自动粘贴")
            self.console.print("  [6] 重置为默认")
            self.console.print("  [0] 返回主菜单")
            self.console.print()
            
            choice = Prompt.ask("选择", choices=["0", "1", "2", "3", "4", "5", "6"], default="0")
            
            if choice == "0":
                break
            elif choice == "1":
                current = self.config.get('server_host')
                new_value = Prompt.ask(f"服务器地址 [{current}]", default=current)
                self.config.set('server_host', new_value)
            elif choice == "2":
                current = self.config.get('server_port')
                new_value = Prompt.ask(f"服务器端口 [{current}]", default=str(current))
                self.config.set('server_port', int(new_value))
            elif choice == "3":
                current = self.config.get('hotkey')
                self.console.print(f"\n当前快捷键: [bold]{current}[/bold]")
                new_value = Prompt.ask(f"新快捷键", default=current)
                self.config.set('hotkey', new_value)
                if self._hotkey_listener:
                    self._hotkey_listener.stop()
                self._setup_hotkey()
            elif choice == "4":
                current = self.config.get('language')
                new_value = Prompt.ask(f"语言", default=current, choices=["auto", "zh", "en"])
                self.config.set('language', new_value)
            elif choice == "5":
                current = self.config.get('auto_paste')
                new_value = Confirm.ask(f"自动粘贴到剪贴板", default=current)
                self.config.set('auto_paste', new_value)
            elif choice == "6":
                if Confirm.ask("确定重置所有设置为默认值?", default=False):
                    for k, v in DEFAULT_CONFIG.items():
                        self.config.set(k, v)
                    self.console.print("[green]已重置为默认设置[/green]")
    
    def microphone_menu(self):
        """麦克风选择菜单"""
        self.clear_screen()
        self.show_header()
        
        self.console.print("\n[bold]🎙️ 麦克风选择[/bold]\n")
        
        mics = list_microphones()
        
        if not mics:
            self.console.print("[red]未找到麦克风设备[/red]")
            Prompt.ask("\n按 Enter 返回")
            return
        
        choices = ["返回"]
        for mic in mics:
            label = f"{mic['name']} (采样率: {mic['sample_rate']})"
            choices.append(label)
        
        for i, mic in enumerate(mics):
            self.console.print(f"  [{i+1}] {mic['name']}")
            self.console.print(f"      采样率: {mic['sample_rate']} Hz, 声道: {mic['channels']}")
        
        self.console.print(f"\n当前: {self._get_mic_name()}")
        
        choice = Prompt.ask("\n选择麦克风", choices=[str(i) for i in range(len(choices))], default="0")
        
        idx = int(choice)
        if idx == 0:
            return
        
        if idx > 0 and idx <= len(mics):
            mic = mics[idx - 1]
            self.config.set('microphone', mic['id'])
            self.console.print(f"[green]已选择: {mic['name']}[/green]")
            Prompt.ask("\n按 Enter 返回")
    
    def show_help(self):
        self.clear_screen()
        self.show_header()
        
        help_text = """
[bold]使用说明[/bold]

[yellow]快捷键操作:[/yellow]
  • 按配置的快捷键开始/停止录音
  • 默认快捷键: Alt+Space

[yellow]菜单操作:[/yellow]
  • 输入数字选择菜单项
  • 在设置菜单中可以调整各种参数

[yellow]麦克风选择:[/yellow]
  • 按 5 进入麦克风选择菜单
  • 显示所有可用的输入设备

[yellow]服务器设置:[/yellow]
  • 确保服务端已启动并运行在正确的地址和端口
  • 默认: localhost:6543

[yellow]识别结果:[/yellow]
  • 识别完成后自动复制到剪贴板
  • 可以按 Ctrl+V 粘贴

[yellow]配置文件位置:[/yellow]
  • {config_file}
""".format(config_file=CONFIG_FILE)
        
        self.console.print(Panel(help_text, border_style="blue"))
        Prompt.ask("\n按 Enter 返回")
    
    def _setup_hotkey(self):
        if not HAS_PYNPUT:
            return
        
        hotkey = self.config.get('hotkey')
        
        try:
            if self._hotkey_listener:
                self._hotkey_listener.stop()
            
            # 转换快捷键格式
            hotkey_str = hotkey.lower().replace("ctrl", "<ctrl>").replace("alt", "<alt>").replace("shift", "<shift>")
            
            def on_activate():
                if self.is_recording:
                    self.is_recording = False
                else:
                    asyncio.run_coroutine_threadsafe(self.record_and_transcribe(), self._loop)
            
            self._hotkey_listener = keyboard.GlobalHotKeys({
                hotkey_str: on_activate
            })
            self._hotkey_listener.start()
            logger.info(f"快捷键已注册: {hotkey}")
            
        except Exception as e:
            logger.warning(f"注册快捷键失败: {e}")
    
    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
    
    def run(self):
        self.clear_screen()
        self.show_header()
        self.show_status()
        
        # 启动异步循环
        loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        loop_thread.start()
        
        # 设置快捷键
        self._setup_hotkey()
        
        # 主循环
        while self._running:
            self.show_menu()
            
            try:
                choice = Prompt.ask("选择操作", default="1")
                
                if choice == "1":
                    asyncio.run_coroutine_threadsafe(self.record_and_transcribe(), self._loop)
                    input("\n按 Enter 停止录音...")
                    self.is_recording = False
                elif choice == "2":
                    self.settings_menu()
                    self.clear_screen()
                    self.show_header()
                    self.show_status()
                elif choice == "3":
                    self.clear_screen()
                    self.show_header()
                    self.show_status()
                    Prompt.ask("\n按 Enter 返回")
                elif choice == "4":
                    asyncio.run_coroutine_threadsafe(self.test_connection(), self._loop)
                    Prompt.ask("\n按 Enter 继续")
                elif choice == "5":
                    self.microphone_menu()
                    self.clear_screen()
                    self.show_header()
                    self.show_status()
                elif choice == "6":
                    self.show_help()
                    self.clear_screen()
                    self.show_header()
                    self.show_status()
                elif choice.lower() == "q":
                    self._running = False
                    break
                    
            except KeyboardInterrupt:
                self._running = False
                break
        
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        
        self.console.print("\n[dim]再见！[/dim]")


def main():
    app = VoiceInputCLI()
    app.run()


if __name__ == "__main__":
    main()
