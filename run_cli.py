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
# ]
# ///
"""
Voice Input Framework - 交互式 CLI 客户端

使用 Rich + Questionary 提供美观的交互式界面。
支持快捷键触发录音和完整的设置菜单。

使用方法:
    uv run run_cli.py
"""

import sys
import os
import asyncio
import json
import logging
import threading
import time
from pathlib import Path

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
    "show_notifications": True,
}

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich.prompt import Prompt, Confirm
    from rich.layout import Layout
    import questionary
    from questionary import Style
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    print("请安装 rich 和 questionary: pip install rich questionary")
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
        """清屏"""
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def show_header(self):
        """显示标题"""
        self.console.print(Panel.fit(
            "[bold cyan]🎤 Voice Input Framework[/bold cyan]\n"
            "[dim]交互式语音输入 CLI 客户端[/dim]",
            border_style="cyan"
        ))
    
    def show_status(self):
        """显示状态栏"""
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="white")
        
        table.add_row("服务器", f"{self.config.get('server_host')}:{self.config.get('server_port')}")
        table.add_row("快捷键", f"[bold]{self.config.get('hotkey')}[/bold]")
        table.add_row("语言", self.config.get('language'))
        table.add_row("自动粘贴", "✓" if self.config.get('auto_paste') else "✗")
        
        self.console.print(Panel(table, title="当前配置", border_style="blue"))
    
    def show_menu(self):
        """显示主菜单"""
        self.console.print()
        self.console.print("[bold]操作菜单:[/bold]")
        self.console.print("  [1] 🎤 开始录音")
        self.console.print("  [2] ⚙️  设置")
        self.console.print("  [3] 📋 查看配置")
        self.console.print("  [4] 🔗 测试连接")
        self.console.print("  [5] ❓ 帮助")
        self.console.print("  [q] 🚪 退出")
        self.console.print()
    
    async def test_connection(self):
        """测试服务器连接"""
        import websockets
        
        self.console.print(f"\n[yellow]正在连接 {self.config.server_url}...[/yellow]")
        
        try:
            async with websockets.connect(self.config.server_url, close_timeout=5) as ws:
                # 发送配置
                await ws.send(json.dumps({
                    "type": "config",
                    "language": "auto",
                }))
                
                # 等待响应
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
        """录音并识别"""
        import websockets
        
        if self.is_recording:
            self.console.print("[yellow]已经在录音中...[/yellow]")
            return
        
        self.is_recording = True
        self.console.print()
        self.console.print(Panel(
            "[bold red]🔴 正在录音...[/bold red]\n"
            "[dim]按快捷键或 Enter 停止[/dim]",
            border_style="red"
        ))
        
        full_text = ""
        
        try:
            async with websockets.connect(self.config.server_url) as ws:
                # 发送配置
                await ws.send(json.dumps({
                    "type": "config",
                    "language": self.config.get('language'),
                }))
                
                # 等待就绪
                resp = await ws.recv()
                logger.info(f"Server ready: {resp}")
                
                # 录音循环（这里简化处理，实际需要音频采集）
                self.console.print("[dim]模拟录音中... (实际需要音频输入)[/dim]")
                
                while self.is_recording:
                    await asyncio.sleep(0.1)
                    # 检查是否收到停止信号
                    if not self.is_recording:
                        break
                
                # 发送结束信号
                await ws.send(json.dumps({"type": "end"}))
                
                # 接收结果
                while True:
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        data = json.loads(resp)
                        
                        if data.get("type") == "result":
                            full_text = data.get("text", "")
                            self.console.print(f"\r[green]{full_text}[/green]", end="")
                        elif data.get("type") == "done":
                            break
                            
                    except asyncio.TimeoutError:
                        break
            
            self.console.print()
            
            if full_text:
                self.console.print(Panel(
                    f"[bold green]{full_text}[/bold green]",
                    title="识别结果",
                    border_style="green"
                ))
                
                # 自动粘贴
                if self.config.get('auto_paste'):
                    pyperclip.copy(full_text)
                    self.console.print("[dim]已复制到剪贴板[/dim]")
            else:
                self.console.print("[yellow]未检测到语音内容[/yellow]")
                
        except Exception as e:
            self.console.print(f"[red]录音失败: {e}[/red]")
        
        finally:
            self.is_recording = False
    
    def settings_menu(self):
        """设置菜单"""
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
                self.console.print(f"[green]已更新: {new_value}[/green]")
                time.sleep(1)
            elif choice == "2":
                current = self.config.get('server_port')
                new_value = Prompt.ask(f"服务器端口 [{current}]", default=str(current))
                self.config.set('server_port', int(new_value))
                self.console.print(f"[green]已更新: {new_value}[/green]")
                time.sleep(1)
            elif choice == "3":
                current = self.config.get('hotkey')
                self.console.print(f"\n当前快捷键: [bold]{current}[/bold]")
                self.console.print("[dim]常用: alt+space, ctrl+space, f12, ctrl+shift+v[/dim]")
                new_value = Prompt.ask(f"新快捷键", default=current)
                self.config.set('hotkey', new_value)
                self.console.print(f"[green]已更新: {new_value}[/green]")
                if self._hotkey_listener:
                    self._hotkey_listener.stop()
                self._setup_hotkey()
                time.sleep(1)
            elif choice == "4":
                current = self.config.get('language')
                self.console.print(f"\n当前语言: [bold]{current}[/bold]")
                self.console.print("[dim]可选: auto (自动), zh (中文), en (英文)[/dim]")
                new_value = Prompt.ask(f"语言", default=current, choices=["auto", "zh", "en"])
                self.config.set('language', new_value)
                self.console.print(f"[green]已更新: {new_value}[/green]")
                time.sleep(1)
            elif choice == "5":
                current = self.config.get('auto_paste')
                new_value = Confirm.ask(f"自动粘贴到剪贴板", default=current)
                self.config.set('auto_paste', new_value)
                self.console.print(f"[green]已更新: {'开启' if new_value else '关闭'}[/green]")
                time.sleep(1)
            elif choice == "6":
                if Confirm.ask("确定重置所有设置为默认值?", default=False):
                    self.config.data = DEFAULT_CONFIG.copy()
                    self.config.save()
                    self.console.print("[green]已重置为默认设置[/green]")
                    time.sleep(1)
    
    def show_help(self):
        """显示帮助"""
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

[yellow]服务器设置:[/yellow]
  • 确保服务端已启动并运行在正确的地址和端口
  • 默认: localhost:6543

[yellow]识别结果:[/yellow]
  • 识别完成后自动复制到剪贴板
  • 如果开启自动粘贴，可按 Ctrl+V 粘贴

[yellow]配置文件位置:[/yellow]
  • {config_file}
""".format(config_file=CONFIG_FILE)
        
        self.console.print(Panel(help_text, border_style="blue"))
        Prompt.ask("\n按 Enter 返回")
    
    def _setup_hotkey(self):
        """设置全局快捷键"""
        if not HAS_PYNPUT:
            return
        
        hotkey = self.config.get('hotkey')
        
        # 解析快捷键
        try:
            # 停止旧的监听器
            if self._hotkey_listener:
                self._hotkey_listener.stop()
            
            # 转换快捷键格式
            # alt+space -> <alt>+ 
            # ctrl+shift+v -> <ctrl>+<shift>+v
            hotkey_str = hotkey.lower().replace("ctrl", "<ctrl>").replace("alt", "<alt>").replace("shift", "<shift>")
            if "space" in hotkey_str:
                hotkey_str = hotkey_str.replace("space", " ")
            
            def on_activate():
                if self.is_recording:
                    self.is_recording = False
                else:
                    asyncio.run_coroutine_threadsafe(self.record_and_transcribe(), self._loop)
            
            self._hotkey_listener = keyboard.GlobalHotKeys({
                hotkey_str: on_activate
            })
            self._hotkey_listener.start()
            logger.info(f"快捷键已注册: {hotkey} -> {hotkey_str}")
            
        except Exception as e:
            logger.warning(f"注册快捷键失败: {e}")
    
    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
    
    def run(self):
        """运行应用"""
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
        
        # 清理
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        
        self.console.print("\n[dim]再见！[/dim]")


def main():
    app = VoiceInputCLI()
    app.run()


if __name__ == "__main__":
    main()
