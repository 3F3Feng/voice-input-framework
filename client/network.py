#!/usr/bin/env python3
"""
客户端网络模块 — STT/LLM 服务通信

从 gui.py 拆分出来的网络相关职责：
- STT 服务器连接与验证
- WebSocket 流式音频传输
- HTTP REST API 调用（模型管理、LLM 后处理）
- 模型加载状态轮询
- LLM 提示词管理
"""

import asyncio
import base64
import json
import logging
import time
from typing import Optional, Callable, Any

from .websocket_keepalive import WebSocketKeepAlive, ConnectionState

logger = logging.getLogger(__name__)

# 超时默认值
DEFAULT_WS_CLOSE_TIMEOUT = 10
DEFAULT_WS_PING_INTERVAL = 20
DEFAULT_WS_PING_TIMEOUT = 10
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READY_TIMEOUT = 30.0
DEFAULT_RESULT_TIMEOUT = 300.0   # 5 分钟（大模型如 qwen_asr 需要较长时间）
DEFAULT_HTTP_TIMEOUT = 10.0
DEFAULT_MODEL_SWITCH_TIMEOUT = 300.0  # 5 分钟（大模型加载需要时间）
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_MAX_POLLS = 300  # 最多轮询 300 次 × 2 秒 = 10 分钟


class SttClient:
    """STT 语音识别服务客户端 — 封装 WebSocket/HTTP 通信逻辑"""

    def __init__(self, host: str = "localhost", port: int = 6544):
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}/ws/stream"
        self.http_url = f"http://{host}:{port}"

        # 连接状态
        self.ws = None
        self.keepalive: Optional[WebSocketKeepAlive] = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.is_connected = False

        # 模型信息
        self.available_models: list[str] = []
        self.current_model: Optional[str] = None

        # 流式传输结果
        self.stream_result: Optional[str] = None
        self.stream_error: Optional[str] = None

        # 回调（GUI 集成用）
        self.on_log: Optional[Callable[[str], None]] = None
        self.on_status: Optional[Callable[[str, str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_models_updated: Optional[Callable[[list, str], None]] = None
        self.on_llm_start: Optional[Callable[[str], None]] = None

    def _log(self, msg: str):
        """统一日志输出"""
        logger.info(msg)
        if self.on_log:
            self.on_log(msg)

    # ──────────────────── 连接管理 ────────────────────

    async def connect(self) -> bool:
        """连接到 STT 服务器并验证

        创建临时 WebSocket 连接测试连通性，获取服务器模型信息，
        验证成功后关闭临时连接。

        Returns:
            bool: 是否连接成功
        """
        import websockets

        self._log(f"连接到 {self.ws_url}...")
        if self.on_status:
            self.on_status("连接中...", "yellow")

        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    self.ws_url,
                    close_timeout=5,
                    ping_interval=DEFAULT_WS_PING_INTERVAL,
                    ping_timeout=DEFAULT_WS_PING_TIMEOUT,
                ),
                timeout=DEFAULT_CONNECT_TIMEOUT,
            )

            # 等待服务器准备就绪
            ready_msg = await asyncio.wait_for(self.ws.recv(), timeout=DEFAULT_READY_TIMEOUT)
            data = json.loads(ready_msg)

            if data.get("type") == "ready":
                model = data.get("model", "unknown")
                is_loading = data.get("is_loading", False)
                self._log(f"✓ 已连接，服务器模型: {model}")

                if is_loading:
                    self._log(f"⚠️ 模型 {model} 正在加载中，切换模型可能会有延迟")
                    if self.on_status:
                        self.on_status(f"已连接 ({model} 加载中...)", "yellow")
                else:
                    if self.on_status:
                        self.on_status(f"已连接 ({model})", "green")

                self.is_connected = True
                self.current_model = model

                # 关闭测试连接，后续会为每次转写创建新连接
                try:
                    await self.ws.close()
                except Exception:
                    pass
                self.ws = None

                return True
            else:
                self._log(f"✗ 服务器响应错误: {data}")
                if self.on_status:
                    self.on_status("连接失败", "red")
                try:
                    await self.ws.close()
                except Exception:
                    pass
                self.ws = None
                return False

        except asyncio.TimeoutError:
            self._log("✗ 连接超时")
            if self.on_status:
                self.on_status("连接超时", "red")
            return False
        except Exception as e:
            self._log(f"✗ 连接失败: {e}")
            if self.on_status:
                self.on_status("连接失败", "red")
            return False

    async def disconnect(self):
        """断开 STT 连接"""
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        self.is_connected = False
        self.connection_state = ConnectionState.DISCONNECTED

    # ──────────────────── 模型管理 ────────────────────

    async def fetch_models(self) -> bool:
        """获取服务器上的可用模型列表

        Returns:
            bool: 是否成功获取到模型列表
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
                url = f"{self.http_url}/models"
                self._log(f"正在获取模型列表 from {url}...")

                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        try:
                            response_data = resp.json()
                            self._parse_models_response(response_data)

                            if self.available_models:
                                self._log(f"✓ 获取到模型列表: {', '.join(self.available_models)}")
                            else:
                                self._log(f"⚠️ 响应中未找到模型，响应完整内容: {response_data}")

                            if self.on_models_updated:
                                self.on_models_updated(self.available_models, self.current_model or "")

                            return bool(self.available_models)
                        except json.JSONDecodeError as e:
                            self._log(f"✗ JSON 解析失败: {e}")
                            return False
                    else:
                        self._log(f"✗ 获取模型失败: HTTP {resp.status_code}")
                        return False
                except Exception as e:
                    self._log(f"✗ HTTP 请求失败: {e}")
                    return False
        except ImportError:
            self._log("⚠️ 需要安装 httpx: pip install httpx")
            return False
        except Exception as e:
            self._log(f"✗ 获取模型失败: {e}")
            return False

    def _parse_models_response(self, response_data: Any):
        """解析模型列表响应（兼容多种格式）"""
        self.available_models = []

        if isinstance(response_data, list):
            # 列表格式
            for m in response_data:
                if isinstance(m, dict):
                    name = m.get("name", "")
                    if name:
                        self.available_models.append(name)
                    if m.get("is_loaded", False):
                        self.current_model = name
        elif isinstance(response_data, dict):
            # 字典格式，可能带有 "models" 键
            if "models" in response_data:
                self.available_models = [
                    m.get("name", "") for m in response_data.get("models", [])
                ]
                for m in response_data.get("models", []):
                    if m.get("is_loaded", False):
                        self.current_model = m.get("name", "")
                        break

        # 过滤空字符串
        self.available_models = [m for m in self.available_models if m]

    async def switch_model(self, model_name: str) -> bool:
        """切换 STT 模型

        Args:
            model_name: 目标模型名称

        Returns:
            bool: 是否切换成功
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_MODEL_SWITCH_TIMEOUT) as client:
                url = f"{self.http_url}/models/select"
                data = {"model_name": model_name}

                self._log(f"正在切换到模型: {model_name}，请等待（qwen_asr 模型较大，需几分钟）...")

                resp = await client.post(url, data=data)
                if resp.status_code == 200:
                    result = resp.json()
                    self.current_model = model_name
                    is_loading = result.get("is_loading", False)

                    if is_loading:
                        self._log(f"✓ 切换请求已接受，模型 {model_name} 正在后台加载")
                        return True
                    else:
                        self._log(f"✓ 已切换到模型: {model_name}")
                        if self.on_models_updated:
                            self.on_models_updated(self.available_models, model_name)
                        return True
                elif resp.status_code == 408:
                    self._log("✗ 切换模型超时: 模型加载时间过长")
                    return False
                else:
                    self._log(f"✗ 切换模型失败: HTTP {resp.status_code}")
                    return False
        except ImportError:
            self._log("⚠️ 需要安装 httpx: pip install httpx")
            return False
        except Exception as e:
            self._log(f"✗ 切换模型失败: {e}")
            return False

    async def poll_model_loading_status(self, model_name: str) -> bool:
        """轮询检查模型加载状态

        每 2 秒检查一次，最多轮询 300 次（10 分钟）。
        加载完成后更新 current_model 并触发回调。

        Args:
            model_name: 要检查的模型名称

        Returns:
            bool: 模型是否加载完成
        """
        import httpx

        self._log(f"开始轮询模型 {model_name} 的加载状态...")
        poll_count = 0

        while poll_count < DEFAULT_MAX_POLLS:
            await asyncio.sleep(DEFAULT_POLL_INTERVAL)
            poll_count += 1

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
                    url = f"{self.http_url}/models/status/{model_name}"
                    resp = await client.get(url)

                    if resp.status_code == 200:
                        data = resp.json()
                        is_loading = data.get("is_loading", False)

                        if not is_loading:
                            self._log(f"✓ 模型 {model_name} 加载完成！")
                            self.current_model = model_name
                            if self.on_status:
                                self.on_status(f"已连接 ({model_name})", "green")
                            if self.on_models_updated:
                                self.on_models_updated(self.available_models, model_name)
                            return True
                        else:
                            loading_since = data.get("loading_since")
                            if loading_since:
                                elapsed = time.time() - loading_since
                                self._log(f"模型 {model_name} 加载中... ({elapsed:.0f}s)")
                    else:
                        self._log(f"⚠️ 检查模型状态失败: HTTP {resp.status_code}")
            except Exception as e:
                self._log(f"⚠️ 轮询模型状态出错: {e}")
                continue

        self._log(f"⚠️ 轮询超时: 模型 {model_name} 加载时间过长")
        return False

    # ──────────────────── 音频转写 ────────────────────

    async def send_audio(self, audio_buffer: list[bytes]) -> Optional[str]:
        """发送完整音频到服务器并获取识别结果（备用模式：录完再发）

        Args:
            audio_buffer: 音频 PCM 数据块列表

        Returns:
            Optional[str]: 识别结果文本，失败返回 None
        """
        import websockets

        if not audio_buffer:
            self._log("没有音频数据")
            return None

        if not self.is_connected:
            self._log("未连接到服务器，正在重新连接...")
            if not await self.connect():
                return None

        try:
            # 创建新的 WebSocket 连接用于此次转写
            self._log("正在连接到服务器...")
            ws = await asyncio.wait_for(
                websockets.connect(
                    self.ws_url,
                    close_timeout=DEFAULT_WS_CLOSE_TIMEOUT,
                    ping_interval=DEFAULT_WS_PING_INTERVAL,
                    ping_timeout=DEFAULT_WS_PING_TIMEOUT,
                ),
                timeout=15.0,
            )

            # 等待服务器准备就绪
            self._log("等待服务器准备就绪...")
            ready_msg = await asyncio.wait_for(ws.recv(), timeout=DEFAULT_READY_TIMEOUT)
            data = json.loads(ready_msg)

            if data.get("type") != "ready":
                self._log(f"服务器没有准备就绪: {data}")
                await ws.close()
                return None

            ready_model = data.get("model", "unknown")
            is_loading = data.get("is_loading", False)
            self._log(f"服务器准备就绪，当前模型: {ready_model}")

            if is_loading:
                self._log("⚠️ 模型正在加载中，可能需要等待...")

            # 发送配置消息
            await ws.send(json.dumps({"type": "config", "language": "auto"}))

            # 合并音频数据
            full_audio = b"".join(audio_buffer)
            audio_size_kb = len(full_audio) / 1024
            self._log(f"发送 {audio_size_kb:.1f} KB 音频...")

            # 发送音频消息
            await ws.send(json.dumps({
                "type": "audio",
                "data": base64.b64encode(full_audio).decode(),
            }))

            # 发送结束信号
            await ws.send(json.dumps({"type": "end"}))

            # 接收结果
            self._log("等待识别结果...")
            result_text = ""

            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=DEFAULT_RESULT_TIMEOUT)
                    data = json.loads(response)
                    msg_type = data.get("type")

                    if msg_type == "result":
                        result_text = data.get("text", "")
                        llm_latency = data.get("llm_latency_ms")
                        if llm_latency is not None:
                            self._log(f"识别结果: {result_text} (LLM: {llm_latency:.0f}ms)")
                        else:
                            self._log(f"识别结果: {result_text}")

                    elif msg_type == "done":
                        self._log("识别完成")
                        await ws.close()
                        return result_text

                    elif msg_type == "error":
                        error_msg = data.get("error_message", "未知错误")
                        error_code = data.get("error_code", "")
                        self._log(f"识别错误 [{error_code}]: {error_msg}")
                        await ws.close()
                        return None

                except asyncio.TimeoutError:
                    self._log("识别超时（5分钟） - 模型可能还在加载中")
                    await ws.close()
                    return None

        except asyncio.TimeoutError:
            self._log("连接超时")
            return None
        except Exception as e:
            self._log(f"发送音频失败: {e}")
            return None

    async def stream_audio(self, audio_queue: asyncio.Queue, language: str = "auto") -> Optional[str]:
        """流式发送音频到服务器（边录边发）

        从 audio_queue 中逐块读取音频数据，通过 WebSocket 实时发送。
        audio_queue 中的 None 值表示录音结束。

        Args:
            audio_queue: 音频数据块队列，None 为结束信号
            language: 识别语言

        Returns:
            Optional[str]: 识别结果文本，失败返回 None
        """
        import websockets

        try:
            self._log("建立 WebSocket 连接...")
            async with websockets.connect(
                self.ws_url,
                close_timeout=DEFAULT_WS_CLOSE_TIMEOUT,
                ping_interval=DEFAULT_WS_PING_INTERVAL,
                ping_timeout=DEFAULT_WS_PING_TIMEOUT,
            ) as ws:
                # 发送配置
                await ws.send(json.dumps({"type": "config", "language": language}))
                self._log(f"已发送配置 (language: {language})")

                # 等待准备就绪
                try:
                    ready_msg = await asyncio.wait_for(ws.recv(), timeout=DEFAULT_READY_TIMEOUT)
                    data = json.loads(ready_msg)
                    if data.get("type") == "ready":
                        self._log("服务器已准备就绪，开始流式传输...")
                    else:
                        self._log(f"服务器响应异常: {data}")
                        return None
                except asyncio.TimeoutError:
                    self._log("等待服务器准备超时")
                    return None

                # 流式发送音频块
                while True:
                    try:
                        chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                        if chunk is None:
                            # 收到结束信号
                            break
                        # 发送音频块
                        await ws.send(json.dumps({
                            "type": "audio",
                            "data": base64.b64encode(chunk).decode(),
                        }))
                    except asyncio.TimeoutError:
                        # 队列为空但可能还在录音，继续等待
                        continue
                    except Exception as e:
                        self._log(f"发送音频块出错: {e}")
                        break

                # 发送结束信号
                self._log("发送结束信号...")
                await ws.send(json.dumps({"type": "end"}))

                # 等待识别结果
                self._log("等待识别结果...")
                result_text = ""
                error_msg = None

                while True:
                    try:
                        response = await asyncio.wait_for(ws.recv(), timeout=DEFAULT_RESULT_TIMEOUT)
                        data = json.loads(response)
                        msg_type = data.get("type")

                        if msg_type == "result":
                            result_text = data.get("text", "")
                            llm_latency = data.get("llm_latency_ms")
                            llm_model = data.get("llm_model", "")
                            if llm_latency is not None:
                                self._log(f"识别结果: {result_text} (LLM: {llm_latency:.0f}ms)")
                            else:
                                self._log(f"识别结果: {result_text}")

                        elif msg_type == "llm_start":
                            original_text = data.get("text", "")
                            self._log(f"LLM处理中: {original_text[:30]}...")
                            if self.on_llm_start:
                                self.on_llm_start(original_text)

                        elif msg_type == "done":
                            self._log("识别完成")
                            self.stream_result = result_text
                            self.stream_error = None
                            return result_text

                        elif msg_type == "error":
                            error_msg = data.get("error_message", "未知错误")
                            error_code = data.get("error_code", "")
                            self._log(f"识别错误 [{error_code}]: {error_msg}")
                            self.stream_result = None
                            self.stream_error = error_msg
                            return None

                    except asyncio.TimeoutError:
                        self._log("等待结果超时")
                        self.stream_error = "等待结果超时"
                        return None

        except Exception as e:
            self._log(f"流式传输出错: {e}")
            self.stream_error = str(e)
            return None

    # ──────────────────── LLM 启用状态 ────────────────────

    async def update_llm_enabled(self, enabled: bool) -> bool:
        """更新 LLM 后处理启用状态

        Args:
            enabled: 是否启用 LLM 后处理

        Returns:
            bool: 是否更新成功
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.put(
                    f"{self.http_url}/llm/enabled",
                    json={"enabled": enabled},
                )
                if resp.status_code == 200:
                    self._log(f"✓ LLM后处理已{'启用' if enabled else '禁用'}")
                    return True
                else:
                    self._log(f"✗ 更新失败: {resp.status_code}")
                    return False
        except Exception as e:
            self._log(f"✗ 更新LLM状态失败: {e}")
            return False

    async def get_llm_enabled(self) -> bool:
        """查询 LLM 后处理是否启用

        Returns:
            bool: LLM 后处理是否启用
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.http_url}/llm/enabled")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("enabled", True)
        except Exception as e:
            self._log(f"查询 LLM 状态失败: {e}")
        return True


class LlmClient:
    """LLM 后处理服务客户端 — 模型切换和提示词管理"""

    def __init__(self, http_url: str):
        """初始化 LLM 客户端

        Args:
            http_url: 服务器 HTTP 基础 URL（与 STT 服务共享同一端口）
        """
        self.http_url = http_url
        self.available_models: list[str] = []
        self.current_model: Optional[str] = None

        # 回调
        self.on_log: Optional[Callable[[str], None]] = None

    def _log(self, msg: str):
        """统一日志输出"""
        logger.info(msg)
        if self.on_log:
            self.on_log(msg)

    # ──────────────────── 模型管理 ────────────────────

    async def fetch_models(self) -> bool:
        """获取可用的 LLM 模型列表

        Returns:
            bool: 是否成功获取
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
                url = f"{self.http_url}/llm/models"
                self._log(f"正在获取LLM模型列表 from {url}...")
                resp = await client.get(url)

                if resp.status_code == 200:
                    data = resp.json()

                    # 提取模型名称列表
                    models = data.get("models", [])
                    self.available_models = [
                        m.get("name") if isinstance(m, dict) else m for m in models
                    ]

                    # 获取当前模型
                    self.current_model = data.get("current_model", "")

                    # 兼容旧格式
                    if not self.current_model and models:
                        for m in models:
                            if isinstance(m, dict) and m.get("is_current"):
                                self.current_model = m.get("name", "")
                                break
                    if not self.current_model and models and isinstance(models[0], dict):
                        self.current_model = models[0].get("name", "")
                    elif not self.current_model and models:
                        self.current_model = models[0] if isinstance(models[0], str) else ""

                    llm_enabled = data.get("enabled", True)
                    self._log(f"✓ 获取到LLM模型列表: {', '.join(self.available_models)}")
                    self._log(f"  当前LLM模型: {self.current_model}, 启用: {llm_enabled}")
                    return True
                else:
                    self._log(f"⚠️ 获取LLM模型失败: HTTP {resp.status_code}")
                    return False
        except Exception as e:
            self._log(f"⚠️ 获取LLM模型出错: {e}")
            return False

    async def switch_model(self, model_name: str) -> bool:
        """切换 LLM 模型

        Args:
            model_name: 目标模型名称

        Returns:
            bool: 是否切换成功
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
                url = f"{self.http_url}/llm/models/select"
                self._log(f"正在切换LLM模型到: {model_name}...")

                resp = await client.post(url, json={"model_name": model_name})
                if resp.status_code == 200:
                    data = resp.json()
                    success = data.get("status") == "success"
                    current = data.get("current_model", "")

                    if success:
                        self.current_model = current
                        self._log(f"✓ LLM模型切换成功: {current}")
                    else:
                        self._log(f"✗ LLM模型切换失败: {data.get('message', 'Unknown error')}")
                    return success
                else:
                    self._log(f"⚠️ LLM模型切换失败: HTTP {resp.status_code}")
                    return False
        except Exception as e:
            self._log(f"⚠️ 切换LLM模型出错: {e}")
            return False

    # ──────────────────── 提示词管理 ────────────────────

    async def load_prompt(self) -> Optional[str]:
        """加载 LLM 提示词

        Returns:
            Optional[str]: 提示词内容，失败返回 None
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
                resp = await client.get(f"{self.http_url}/llm/prompt")
                if resp.status_code == 200:
                    data = resp.json()
                    prompt = data.get("prompt", "")
                    self._log("LLM提示词已加载")
                    return prompt
                else:
                    self._log(f"加载LLM提示词失败: HTTP {resp.status_code}")
                    return None
        except Exception as e:
            self._log(f"加载LLM提示词出错: {e}")
            return None

    async def save_prompt(self, prompt: str) -> bool:
        """保存 LLM 提示词

        Args:
            prompt: 提示词内容

        Returns:
            bool: 是否保存成功
        """
        import httpx

        if not prompt.strip():
            self._log("提示词不能为空")
            return False

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
                resp = await client.put(
                    f"{self.http_url}/llm/prompt",
                    json={"prompt": prompt},
                )
                if resp.status_code == 200:
                    self._log("LLM提示词已保存")
                    return True
                else:
                    self._log(f"保存LLM提示词失败: HTTP {resp.status_code}")
                    return False
        except Exception as e:
            self._log(f"保存LLM提示词出错: {e}")
            return False
