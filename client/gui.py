#!/usr/bin/env python3
"""
Voice Input Framework - 快捷键驱动的语音输入客户端

按住快捷键说话，松开快捷键后自动将识别结果输入到当前窗口。

功能：
- 快捷键控制（默认 Alt+V）
- 实时录音
- WebSocket 流式识别  
- 自动将结果输入活跃窗口
- 模型动态切换
- 错误信息实时显示
- 麦克风选择

要求:
pip install pynput sounddevice websockets httpx pyautogui pyperclip PySimpleGUI
"""

import asyncio
import base64
import json
import logging
import sys
import threading
import time
from datetime import datetime
from typing import Optional

import PySimpleGUI as sg
from pynput import keyboard

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_SERVER_HOST = "100.124.8.85"
DEFAULT_SERVER_PORT = 6543
DEFAULT_HOTKEY = "alt+v"

# 音频参数
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_SIZE = 1024


class HotkeyVoiceInput:
    """快捷键驱动的语音输入客户端"""
    
    def __init__(self, server_host: str = DEFAULT_SERVER_HOST, server_port: int = DEFAULT_SERVER_PORT):
        self.server_host = server_host
        self.server_port = server_port
        self.server_url = f"ws://{server_host}:{server_port}/ws/stream"
        self.rest_api_url = f"http://{server_host}:{server_port}"
        
        # 状态
        self.is_running = False
        self.is_recording = False
        self.is_connected = False
        self.audio_buffer = []
        self.last_result = ""
        self.available_models = []  # 可用的模型列表
        self.current_model = None   # 当前模型
        
        # 资源
        self.stream = None
        self.ws = None
        self.window = None
        
        # 后台线程
        self.hotkey_listener = None
        self.async_loop = None
        self.loop_thread = None
        
        # 快捷键状态追踪
        self._hotkey_pressed = False
        self._pressed_keys = set()
        
        # 麦克风设置
        self.audio_devices = self._get_audio_devices()
        self.selected_device = None  # None 表示用默认设备
        
        self._setup_ui()
    
    def _get_audio_devices(self):
        """获取系统中可用的音频输入设备"""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            
            # 过滤出输入设备
            input_devices = {}
            for i, device in enumerate(devices):
                if device['max_input_channels'] > 0:
                    input_devices[i] = f"{device['name']}"
            
            return input_devices if input_devices else {-1: "默认设备"}
        except Exception as e:
            logger.warning(f"获取音频设备失败: {e}")
            return {-1: "默认设备"}
    
    def _setup_ui(self):
        """创建用户界面"""
        sg.theme("DarkBlue3")
        
        layout = [
            [sg.Text("🎤 Voice Input", font=("Helvetica", 14, "bold"), justification="center", expand_x=True)],
            [sg.HorizontalSeparator()],
            
            # 连接状态
            [sg.Text(f"服务器: {self.server_host}:{self.server_port}", size=(50, 1)),
             sg.Text("未连接", key="-STATUS-", text_color="red", size=(15, 1))],
            
            # 快捷键设置
            [sg.Frame("快捷键设置", [
                [sg.Text("开始/停止录音:"), sg.Input(DEFAULT_HOTKEY, key="-HOTKEY-", size=(20, 1)),
                 sg.Button("更新", key="-UPDATE-HOTKEY-", size=(8, 1))],
                [sg.Text("(按住快捷键说话，松开后自动输入)", text_color="gray", font=("Helvetica", 9))],
            ])],
            
            # 麦克风选择
            [sg.Frame("麦克风设置", [
                [sg.Text("麦克风:"), 
                 sg.Combo(list(self.audio_devices.values()), 
                         default_value=self.audio_devices.get(list(self.audio_devices.keys())[0], "默认设备"),
                         key="-MICROPHONE-", 
                         size=(50, 1),
                         readonly=True)],
            ])],
            
            # 服务器配置
            [sg.Frame("服务器配置", [
                [sg.Text("主机:"), sg.Input(self.server_host, key="-HOST-", size=(20, 1)),
                 sg.Text("端口:"), sg.Input(str(self.server_port), key="-PORT-", size=(8, 1))],
                [sg.Button("连接", key="-CONNECT-", button_color=("white", "green"), size=(10, 1)),
                 sg.Text("", key="-CONN-STATUS-", text_color="yellow")],
            ])],
            
            # 模型选择
            [sg.Frame("模型设置", [
                [sg.Text("选择模型:"), 
                 sg.Combo([], default_value="", key="-MODEL-SELECT-", size=(30, 1), readonly=True),
                 sg.Button("🔄 刷新", key="-REFRESH-MODELS-", size=(8, 1)),
                 sg.Button("切换", key="-SWITCH-MODEL-", button_color=("white", "blue"), size=(8, 1))],
                [sg.Text("", key="-MODEL-STATUS-", text_color="yellow", size=(70, 1))],
            ])],
            
            # 错误信息显示
            [sg.Frame("错误信息", [
                [sg.Multiline("", key="-ERROR-", size=(80, 3), font=("Consolas", 8),
                          autoscroll=True, disabled=True, background_color="#3e1e1e", text_color="#ff8888")],
            ])],
            
            # 识别结果
            [sg.Frame("识别结果", [
                [sg.Multiline("", key="-RESULT-", size=(70, 8), font=("Consolas", 10),
                          autoscroll=True, disabled=True, background_color="#1e1e1e", text_color="white")],
                [sg.Button("📋 复制", key="-COPY-", size=(10, 1)),
                 sg.Button("🗑️ 清空", key="-CLEAR-", size=(10, 1)),
                 sg.Button("✏️ 输入（自动）", key="-PASTE-", size=(15, 1))],
            ])],
            
            # 日志
            [sg.Frame("日志", [
                [sg.Multiline("", key="-LOG-", size=(80, 5), font=("Consolas", 8),
                          autoscroll=True, disabled=True, background_color="#1e1e1e", text_color="#aaaaaa")],
            ])],
            
            [sg.Button("退出", key="-EXIT-", button_color=("white", "gray"), size=(10, 1))],
        ]
        
        self.window = sg.Window("🎤 Voice Input Framework", layout, finalize=True, keep_on_top=True)
        self.is_running = True
    
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
        self.last_result = text
    
    def set_status(self, status: str, color: str = "yellow"):
        """更新连接状态"""
        if self.window:
            self.window["-STATUS-"].update(status, text_color=color)
    
    async def connect_to_server(self) -> bool:
        """连接到服务器并验证连接"""
        try:
            import websockets
            
            self.log(f"连接到 {self.server_url}...")
            self.set_status("连接中...", "yellow")
            
            # 创建临时连接来测试
            self.ws = await asyncio.wait_for(
                websockets.connect(self.server_url, close_timeout=5),
                timeout=10.0
            )
            
            # 等待服务器准备就绪
            ready_msg = await asyncio.wait_for(self.ws.recv(), timeout=30.0)
            data = json.loads(ready_msg)
            
            if data.get("type") == "ready":
                model = data.get("model", "unknown")
                is_loading = data.get("is_loading", False)
                self.log(f"✓ 已连接，服务器模型: {model}")
                
                if is_loading:
                    self.log(f"⚠️ 模型 {model} 正在加载中，切换模型可能会有延迟")
                    self.set_status(f"已连接 ({model} 加载中...)", "yellow")
                else:
                    self.set_status(f"已连接 ({model})", "green")
                
                self.is_connected = True
                self.current_model = model
                
                # 关闭测试连接，后续会为每次转写创建新连接
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None
                
                # 自动获取模型列表
                await self.fetch_models()
                
                return True
            else:
                self.log(f"✗ 服务器响应错误: {data}")
                self.set_status("连接失败", "red")
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None
                return False
                
        except asyncio.TimeoutError:
            self.log("✗ 连接超时")
            self.set_status("连接超时", "red")
        except Exception as e:
            self.log(f"✗ 连接失败: {e}")
            self.set_status("连接失败", "red")
        
        return False
    
    async def fetch_models(self):
        """获取服务器上的可用模型列表"""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{self.rest_api_url}/models"
                self.log(f"正在获取模型列表 from {url}...")
                try:
                    resp = await client.get(url)
                    self.log(f"模型列表响应状态: {resp.status_code}")
                    
                    if resp.status_code == 200:
                        try:
                            response_data = resp.json()
                            self.log(f"原始响应数据: {response_data}")
                            
                            # 处理响应格式
                            if isinstance(response_data, list):
                                # 列表格式
                                self.available_models = []
                                for m in response_data:
                                    if isinstance(m, dict):
                                        name = m.get("name", "")
                                        if name:
                                            self.available_models.append(name)
                                    elif hasattr(m, 'name'):
                                        # 如果是对象
                                        self.available_models.append(m.name)
                            elif isinstance(response_data, dict):
                                # 字典格式，可能带有 "models" 键
                                if "models" in response_data:
                                    self.available_models = [m.get("name", "") for m in response_data.get("models", [])]
                                else:
                                    self.available_models = []
                            else:
                                self.available_models = []
                            
                            # 过滤掉空字符串
                            self.available_models = [m for m in self.available_models if m]
                            
                            if self.available_models:
                                self.log(f"✓ 获取到模型列表: {', '.join(self.available_models)}")
                            else:
                                self.log(f"⚠️ 响应中未找到模型，响应完整内容: {response_data}")
                                self.show_error(f"未找到可用的模型。服务器响应: {response_data}")
                            
                            # 更新UI下拉菜单
                            if self.window and self.available_models:
                                # 使用update()方法更新Combo的值
                                self.window["-MODEL-SELECT-"].update(values=self.available_models, value=self.available_models[0])
                                self.current_model = self.available_models[0]
                                self.window["-MODEL-STATUS-"].update(f"当前模型: {self.current_model}", text_color="yellow")
                            elif self.window:
                                self.log("没有可用的模型")
                                self.window["-MODEL-SELECT-"].update(values=[], value="")
                                self.window["-MODEL-STATUS-"].update("未找到可用模型", text_color="red")
                            
                            return bool(self.available_models)
                        except json.JSONDecodeError as e:
                            self.log(f"✗ JSON 解析失败: {e}")
                            self.log(f"响应内容: {resp.text}")
                            self.show_error(f"响应不是有效 JSON: {resp.text[:200]}")
                            return False
                    else:
                        error_text = resp.text
                        self.log(f"✗ 获取模型失败: HTTP {resp.status_code}")
                        self.log(f"错误响应: {error_text}")
                        self.show_error(f"获取模型失败: HTTP {resp.status_code}\n{error_text[:200]}")
                        return False
                except Exception as e:
                    import traceback
                    self.log(f"✗ HTTP 请求失败: {e}")
                    self.log(f"错误堆栈: {traceback.format_exc()}")
                    self.show_error(f"HTTP 请求失败: {e}")
                    return False
        except ImportError:
            self.log("⚠️ 需要安装 httpx: pip install httpx")
            self.show_error("需要安装 httpx:\npip install httpx")
            return False
        except Exception as e:
            import traceback
            self.log(f"✗ 获取模型失败: {e}")
            self.log(f"错误堆栈: {traceback.format_exc()}")
            self.show_error(f"获取模型失败: {e}")
            return False
    
    async def switch_model(self, model_name: str):
        """切换模型"""
        try:
            import httpx
            # 大幅增加超时时间以允许 qwen_asr 模型（14GB）加载
            async with httpx.AsyncClient(timeout=300.0) as client:  # 5 分钟超时
                url = f"{self.rest_api_url}/models/select"
                data = {"model_name": model_name}
                try:
                    self.log(f"正在切换到模型: {model_name}，请等待（qwen_asr 模型较大，需几分钟）...")
                    if self.window:
                        self.window["-MODEL-STATUS-"].update(f"正在切换到 {model_name}...（请等待）", text_color="yellow")
                    
                    resp = await client.post(url, data=data)
                    if resp.status_code == 200:
                        result = resp.json()
                        self.current_model = model_name
                        is_loading = result.get("is_loading", False)
                        
                        if is_loading:
                            self.log(f"✓ 切换请求已接受，模型 {model_name} 正在后台加载")
                            if self.window:
                                self.window["-MODEL-STATUS-"].update(f"模型 {model_name} 正在加载中...", text_color="yellow")
                        else:
                            self.log(f"✓ 已切换到模型: {model_name}")
                            if self.window:
                                self.window["-MODEL-STATUS-"].update(f"当前模型: {model_name}", text_color="green")
                        return True
                    elif resp.status_code == 408:  # Timeout
                        self.log(f"✗ 切换模型超时: 模型加载时间过长")
                        self.show_error(f"切换模型超时\n{model_name} 模型太大，加载时间超过 5 分钟")
                        return False
                    else:
                        error_text = resp.text
                        self.log(f"✗ 切换模型失败: HTTP {resp.status_code}")
                        self.show_error(f"切换模型失败: HTTP {resp.status_code}\n{error_text}")
                        return False
                except Exception as e:
                    import traceback
                    self.log(f"✗ HTTP 请求失败: {type(e).__name__}: {e}")
                    self.log(f"错误堆栈: {traceback.format_exc()}")
                    self.show_error(f"HTTP 请求失败: {type(e).__name__}: {e}")
                    return False
        except ImportError:
            self.log("⚠️ 需要安装 httpx: pip install httpx")
            self.show_error("需要安装 httpx:\npip install httpx")
            return False
        except Exception as e:
            self.log(f"✗ 切换模型失败: {e}")
            self.show_error(f"切换模型失败: {e}")
            return False
    
    def show_error(self, message: str):
        """显示错误信息"""
        if not self.window:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.window["-ERROR-"].print(f"[{timestamp}] ❌ {message}")
    
    async def async_fetch_models(self):
        """异步获取模型列表（在事件循环中执行）"""
        await self.fetch_models()
    
    async def async_switch_model(self, model_name: str):
        """异步切换模型（在事件循环中执行）"""
        await self.switch_model(model_name)
    
    async def wait_for_model_ready(self, model_name: str, timeout: float = 300.0) -> bool:
        """等待模型加载完成"""
        import httpx
        start_time = time.time()
        check_interval = 5.0  # 每 5 秒检查一次
        
        self.log(f"等待模型 {model_name} 加载完成...")
        
        while time.time() - start_time < timeout:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    url = f"{self.rest_api_url}/models/status/{model_name}"
                    resp = await client.get(url)
                    
                    if resp.status_code == 200:
                        status = resp.json()
                        is_loaded = status.get("is_loaded", False)
                        is_current = status.get("is_current", False)
                        
                        if is_loaded and is_current:
                            elapsed = time.time() - start_time
                            self.log(f"✓ 模型 {model_name} 加载完成（耗时 {elapsed:.1f} 秒）")
                            if self.window:
                                self.window["-MODEL-STATUS-"].update(f"当前模型: {model_name}", text_color="green")
                            return True
                        else:
                            self.log(f"模型状态: loaded={is_loaded}, current={is_current}")
            except Exception as e:
                self.log(f"检查模型状态失败: {e}")
            
            await asyncio.sleep(check_interval)
        
        self.log(f"✗ 等待模型 {model_name} 超时")
        return False
    
    async def send_audio_to_server(self) -> Optional[str]:
        """发送音频到服务器并获取识别结果"""
        if not self.audio_buffer:
            self.log("没有音频数据")
            return None
        
        if not self.is_connected:
            self.log("未连接到服务器，正在重新连接...")
            if not await self.connect_to_server():
                return None
        
        try:
            import websockets
            
            # 创建新的WebSocket连接用于此次转写
            self.log("正在连接到服务器...")
            ws = await asyncio.wait_for(
                websockets.connect(self.server_url, close_timeout=10),
                timeout=15.0
            )
            
            # 等待服务器准备就绪
            self.log("等待服务器准备就绪...")
            ready_msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
            data = json.loads(ready_msg)
            
            if data.get("type") != "ready":
                self.log(f"服务器没有准备就绪: {data}")
                await ws.close()
                return None
            
            ready_model = data.get("model", "unknown")
            is_loading = data.get("is_loading", False)
            self.log(f"服务器准备就绪，当前模型: {ready_model}")
            
            if is_loading:
                self.log("⚠️ 模型正在加载中，可能需要等待...")
            
            # 发送配置消息（服务器期望的第一条消息）
            await ws.send(json.dumps({
                "type": "config",
                "language": "auto"
            }))
            
            # 合并音频数据
            full_audio = b"".join(self.audio_buffer)
            audio_size_kb = len(full_audio) / 1024
            self.log(f"发送 {audio_size_kb:.1f} KB 音频...")
            
            # 发送音频消息
            await ws.send(json.dumps({
                "type": "audio",
                "data": base64.b64encode(full_audio).decode()
            }))
            
            # 发送结束信号
            await ws.send(json.dumps({"type": "end"}))
            
            # 接收结果 - qwen_asr 模型很大，增加超时时间到 5 分钟
            self.log("等待识别结果...")
            result_text = ""
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=300.0)  # 5 分钟超时
                    data = json.loads(response)
                    
                    msg_type = data.get("type")
                    
                    if msg_type == "result":
                        result_text = data.get("text", "")
                        confidence = data.get("confidence", 0)
                        self.log(f"识别结果: {result_text} (置信度: {confidence:.2f})")
                        
                    elif msg_type == "done":
                        self.log("识别完成")
                        await ws.close()
                        return result_text
                        
                    elif msg_type == "error":
                        error_msg = data.get("error_message", "未知错误")
                        error_code = data.get("error_code", "")
                        self.log(f"识别错误 [{error_code}]: {error_msg}")
                        await ws.close()
                        return None
                except asyncio.TimeoutError:
                    self.log("识别超时（5分钟） - 模型可能还在加载中")
                    await ws.close()
                    return None
            
        except asyncio.TimeoutError:
            self.log("连接超时")
        except Exception as e:
            self.log(f"发送音频失败: {e}")
        
        return None
    
    def _start_recording(self):
        """开始录音（在后台线程中）"""
        import sounddevice as sd
        
        self.is_recording = True
        self.audio_buffer = []
        self.log("🔴 开始录音...")
        
        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            if self.is_recording:
                self.audio_buffer.append(indata.copy().tobytes())
        
        try:
            self.stream = sd.InputStream(
                device=self.selected_device,  # 使用选择的麦克风
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype='int16',
                blocksize=AUDIO_CHUNK_SIZE,
                callback=callback
            )
            self.stream.start()
        except Exception as e:
            self.log(f"启动录音失败: {e}")
            self.is_recording = False
    
    def _stop_recording(self):
        """停止录音"""
        self.is_recording = False
        self.log(f"⏹️ 停止录音 ({len(self.audio_buffer)} 个音频块)")
        
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.warning(f"关闭音频流失败: {e}")
            self.stream = None
    
    async def _process_audio(self):
        """处理录制的音频"""
        try:
            self.log("处理音频...")
            result = await self.send_audio_to_server()
            
            if result:
                self.log(f"更新结果显示...")
                self.update_result(result)
                self.log(f"开始自动输入...")
                # 自动输入文本
                await self._auto_input_text(result)
                self.log(f"自动输入完成")
            else:
                self.log("未收到识别结果")
        except Exception as e:
            import traceback
            self.log(f"处理音频出错: {e}")
    
    async def _auto_input_text(self, text: str):
        """自动将文本输入到活跃窗口"""
        try:
            import pyautogui
            
            self.log(f"准备输入文本: {text[:50]}...")
            
            # 短暂延迟，让窗口获得焦点
            await asyncio.sleep(0.5)
            
            # 使用剪贴板粘贴（更可靠，支持特殊字符）
            try:
                import pyperclip
                pyperclip.copy(text)
                self.log("✓ 文本已复制到剪贴板")
                
                # 粘贴文本
                pyautogui.hotkey('ctrl', 'v')
                self.log(f"✓ 已粘贴: {text[:50]}...")
                await asyncio.sleep(0.2)
                
            except ImportError as e:
                self.log(f"⚠️ pyperclip 不可用，使用逐字输入: {e}")
                # 回退到逐字输入
                pyautogui.typewrite(text, interval=0.01)
                self.log(f"✓ 已逐字输入: {text[:50]}...")
                
            except Exception as e:
                self.log(f"粘贴失败: {e}，尝试逐字输入...")
                pyautogui.typewrite(text, interval=0.01)
                self.log(f"✓ 已逐字输入: {text[:50]}...")
                
        except ImportError:
            self.log("⚠️ 需要安装 pyautogui 进行自动输入")
        except Exception as e:
            self.log(f"输入文本失败: {e}")
    
    def _setup_hotkey_listener(self, hotkey_str: str):
        """设置快捷键监听器"""
        try:
            # 移除旧的监听器
            if self.hotkey_listener:
                try:
                    self.hotkey_listener.stop()
                except:
                    pass
            
            self.log(f"设置快捷键: {hotkey_str}")
            
            # 解析快捷键字符串 - 移除< >括号
            cleaned = hotkey_str.lower().replace('<', '').replace('>', '')
            hotkey_parts = [p.strip() for p in cleaned.split("+")]
            
            required_mods = []
            required_char = None
            
            for part in hotkey_parts:
                if part in ("ctrl", "control"):
                    required_mods.append(("ctrl", [keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r]))
                elif part in ("alt",):
                    required_mods.append(("alt", [keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r]))
                elif part in ("shift",):
                    required_mods.append(("shift", [keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r]))
                elif part in ("cmd", "command"):
                    required_mods.append(("cmd", [keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r]))
                else:
                    required_char = part
            
            if not required_char:
                raise ValueError("快捷键必须包含至少一个非修饰符的键")
            
            # Track currently pressed keys
            pressed_keys = set()
            hotkey_active = False
            
            def on_press(key):
                nonlocal hotkey_active
                try:
                    pressed_keys.add(key)
                    
                    if not hotkey_active:
                        # Check all required modifiers are pressed
                        mods_satisfied = True
                        for mod_name, mod_keys in required_mods:
                            if not any(k in pressed_keys for k in mod_keys):
                                mods_satisfied = False
                                break
                        
                        if not mods_satisfied:
                            return
                        
                        # Check the main key
                        has_char = False
                        try:
                            if hasattr(key, 'char') and key.char:
                                if key.char.lower() == required_char:
                                    has_char = True
                        except:
                            pass
                        
                        try:
                            if not has_char and hasattr(key, 'name') and key.name:
                                if key.name.lower() == required_char:
                                    has_char = True
                        except:
                            pass
                        
                        # Also check all pressed keys for the char
                        for k in pressed_keys:
                            if has_char:
                                break
                            try:
                                if hasattr(k, 'char') and k.char:
                                    if k.char.lower() == required_char:
                                        has_char = True
                            except:
                                pass
                            try:
                                if not has_char and hasattr(k, 'name') and k.name:
                                    if k.name.lower() == required_char:
                                        has_char = True
                            except:
                                pass
                        
                        if has_char:
                            hotkey_active = True
                            self.log(f"🎙️ 快捷键激活 - 开始录音!")
                            self._hotkey_pressed = True
                            self._start_recording()
                
                except Exception as e:
                    self.log(f"快捷键按下错误: {e}")
            
            def on_release(key):
                nonlocal hotkey_active
                try:
                    pressed_keys.discard(key)
                    
                    if hotkey_active:
                        has_char = False
                        try:
                            if hasattr(key, 'char') and key.char:
                                if key.char.lower() == required_char:
                                    has_char = True
                        except:
                            pass
                        
                        try:
                            if not has_char and hasattr(key, 'name') and key.name:
                                if key.name.lower() == required_char:
                                    has_char = True
                        except:
                            pass
                        
                        if has_char:
                            hotkey_active = False
                            self.log(f"⏹️ 快捷键释放 - 停止录音!")
                            self._hotkey_pressed = False
                            self._stop_recording()
                            # 异步发送音频
                            if self.async_loop:
                                asyncio.run_coroutine_threadsafe(
                                    self._process_audio(),
                                    self.async_loop
                                )
                
                except Exception as e:
                    self.log(f"快捷键释放错误: {e}")
            
            # 创建监听器
            self.hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self.hotkey_listener.start()
            self.log(f"✓ 快捷键已激活: {hotkey_str}")
            
        except Exception as e:
            self.log(f"✗ 快捷键设置失败: {e}")
    
    def _run_async_loop(self):
        """在后台线程中运行异步事件循环"""
        self.async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.async_loop)
        self.async_loop.run_forever()
    
    def run(self):
        """主界面循环"""
        # 启动异步事件循环
        self.loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.loop_thread.start()
        time.sleep(0.1)  # 等待事件循环启动
        
        # 设置初始快捷键
        self._setup_hotkey_listener(DEFAULT_HOTKEY)
        
        # 自动连接到服务器
        if self.async_loop:
            asyncio.run_coroutine_threadsafe(
                self.connect_to_server(),
                self.async_loop
            )
        
        # 主 UI 循环
        while self.is_running:
            try:
                event, values = self.window.read(timeout=100)
                
                if event == sg.WIN_CLOSED or event == "-EXIT-":
                    break
                
                elif event == "-CONNECT-":
                    host = values.get("-HOST-") or self.server_host
                    port_str = values.get("-PORT-") or str(self.server_port)
                    
                    self.server_host = host
                    try:
                        self.server_port = int(port_str)
                    except ValueError:
                        self.log("端口号必须是整数")
                        self.show_error("端口号必须是整数")
                        continue
                    
                    self.server_url = f"ws://{self.server_host}:{self.server_port}/ws/stream"
                    self.rest_api_url = f"http://{self.server_host}:{self.server_port}"
                    self.is_connected = False
                    
                    if self.async_loop:
                        asyncio.run_coroutine_threadsafe(
                            self.connect_to_server(),
                            self.async_loop
                        )
                
                elif event == "-UPDATE-HOTKEY-":
                    hotkey = values.get("-HOTKEY-", DEFAULT_HOTKEY).strip()
                    self._setup_hotkey_listener(hotkey)
                
                elif event == "-MICROPHONE-":
                    selected_name = values.get("-MICROPHONE-")
                    for device_id, device_name in self.audio_devices.items():
                        if device_name == selected_name:
                            self.selected_device = device_id if device_id != -1 else None
                            self.log(f"✓ 已选择麦克风: {device_name}")
                            break
                
                elif event == "-COPY-":
                    if self.last_result:
                        try:
                            import pyperclip
                            pyperclip.copy(self.last_result)
                            self.log("✓ 已复制到剪贴板")
                        except ImportError:
                            self.log("需要安装 pyperclip")
                
                elif event == "-CLEAR-":
                    self.window["-RESULT-"].update("")
                    self.last_result = ""
                    self.log("已清空结果")
                
                elif event == "-PASTE-":
                    if self.last_result and self.async_loop:
                        asyncio.run_coroutine_threadsafe(
                            self._auto_input_text(self.last_result),
                            self.async_loop
                        )
                
                elif event == "-REFRESH-MODELS-":
                    self.log("正在获取模型列表...")
                    self.window["-MODEL-STATUS-"].update("正在获取模型列表...", text_color="yellow")
                    if self.async_loop:
                        asyncio.run_coroutine_threadsafe(
                            self.async_fetch_models(),
                            self.async_loop
                        )
                
                elif event == "-SWITCH-MODEL-":
                    selected_model = values.get("-MODEL-SELECT-", "").strip()
                    if not selected_model:
                        self.show_error("请先选择一个模型")
                        self.log("❌ 未选择模型")
                    else:
                        self.log(f"正在切换到模型: {selected_model}")
                        self.window["-MODEL-STATUS-"].update(f"正在切换到 {selected_model}...", text_color="yellow")
                        if self.async_loop:
                            asyncio.run_coroutine_threadsafe(
                                self.async_switch_model(selected_model),
                                self.async_loop
                            )
            
            except Exception as e:
                logger.error(f"UI 循环错误: {e}")
        
        # 清理资源
        self._cleanup()
    
    def _cleanup(self):
        """清理资源"""
        self.is_running = False
        
        if self.is_recording:
            self._stop_recording()
        
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass
        
        if self.ws and self.async_loop:
            asyncio.run_coroutine_threadsafe(self.ws.close(), self.async_loop)
        
        if self.async_loop:
            self.async_loop.call_soon_threadsafe(self.async_loop.stop)
        
        if self.window:
            self.window.close()


def main():
    """主程序入口"""
    try:
        import sounddevice
        import websockets
        import httpx
        import pyautogui
        import pynput
    except ImportError as e:
        print(f"缺少依赖: {e}")
        print("\n请运行以下命令安装依赖:")
        print("pip install websockets sounddevice httpx pyautogui pyperclip pynput PySimpleGUI")
        return
    
    # 解析配置
    host = DEFAULT_SERVER_HOST
    port = DEFAULT_SERVER_PORT
    
    # 从环境变量读取配置
    import os
    if "VIF_SERVER_HOST" in os.environ:
        host = os.getenv("VIF_SERVER_HOST")
    if "VIF_SERVER_PORT" in os.environ:
        try:
            port = int(os.getenv("VIF_SERVER_PORT"))
        except ValueError:
            pass
    
    # 创建客户端并运行
    client = HotkeyVoiceInput(server_host=host, server_port=port)
    
    try:
        client.run()
    except KeyboardInterrupt:
        print("\n正在退出...")
    except Exception as e:
        print(f"错误: {e}")
        logger.exception("Unhandled exception")


if __name__ == "__main__":
    main()
