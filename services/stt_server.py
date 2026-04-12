#!/usr/bin/env python3
"""
Voice Input Framework - STT Service
独立的 STT 服务器，使用 qwen_asr 进行语音识别。
运行在独立的 conda 环境: vif-stt (transformers 4.x)
Port: 6544
"""
import asyncio
import base64
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from contextvars import ContextVar

import httpx

# 添加项目路径
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ============== Configuration ==============
STT_HOST = os.getenv("VIF_STT_HOST", "0.0.0.0")
STT_PORT = int(os.getenv("VIF_STT_PORT", "6544"))
STT_MODEL = os.getenv("VIF_STT_MODEL", "qwen_asr")
LOG_LEVEL = os.getenv("VIF_LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT = float(os.getenv("VIF_REQUEST_TIMEOUT", "300.0"))
MAX_RETRIES = int(os.getenv("VIF_MAX_RETRIES", "3"))
RETRY_DELAY = float(os.getenv("VIF_RETRY_DELAY", "1.0"))

# LLM Server Configuration
LLM_SERVER_HOST = os.getenv("VIF_LLM_HOST", "localhost")
LLM_SERVER_PORT = int(os.getenv("VIF_LLM_PORT", "6545"))
LLM_SERVER_URL = f"http://{LLM_SERVER_HOST}:{LLM_SERVER_PORT}"

# LLM Processing Toggle
LLM_ENABLED = os.getenv("VIF_LLM_ENABLED", "true").lower() == "true"

# ============== Context Variables ==============
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

# ============== Structured Logging ==============
class StructuredLogFormatter(logging.Formatter):
    """结构化日志格式化器"""
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 添加请求ID
        req_id = request_id_ctx.get()
        if req_id:
            log_data["request_id"] = req_id
        # 添加额外字段
        if hasattr(record, "extra"):
            log_data.update(record.extra)
        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False, default=str)

# 配置日志
_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
if os.getenv("VIF_LOG_JSON", "").lower() == "true":
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredLogFormatter())
    logging.basicConfig(level=LOG_LEVEL, handlers=[handler])
else:
    logging.basicConfig(level=LOG_LEVEL, format=_log_format)
logger = logging.getLogger("stt-server")

# ============== Data Models ==============
class WordTimestamp(BaseModel):
    """词级别时间戳"""
    word: str
    start: float
    end: float

class TranscriptionResult(BaseModel):
    """转写结果"""
    text: str
    confidence: float = 1.0
    language: str = "auto"
    is_final: bool = True
    stt_latency_ms: float = 0.0
    model: str = ""
    timestamps: Optional[List[WordTimestamp]] = None

class TranscriptionRequest(BaseModel):
    """转写请求"""
    language: str = "auto"
    return_timestamps: bool = False

class ModelInfo(BaseModel):
    """模型信息"""
    name: str
    description: str = ""
    is_loaded: bool = False
    is_default: bool = False

class HealthStatus(BaseModel):
    """健康状态"""
    status: str
    version: str = "1.1.0"
    uptime_seconds: float
    current_model: str
    loaded_models: List[str]
    active_connections: int = 0
    total_requests: int = 0
    failed_requests: int = 0

class ErrorResponse(BaseModel):
    """错误响应"""
    error_code: str
    error_message: str
    request_id: str

# ============== Retry Decorator ==============
def with_retry(max_retries: int = MAX_RETRIES, delay: float = RETRY_DELAY):
    """重试装饰器"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                        await asyncio.sleep(delay * (2 ** attempt))  # 指数退避
                    else:
                        logger.error(f"Failed after {max_retries + 1} attempts: {e}")
                        raise
            raise last_exception
        return wrapper
    return decorator


# ============== LLM Client ==============
async def call_llm_server(text: str, request_id: str = "") -> Tuple[str, float]:
    """调用 LLM 服务器进行后处理
    
    Returns:
        tuple: (processed_text, latency_ms)
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{LLM_SERVER_URL}/process",
                json={"text": text, "options": {}},
                headers={"X-Request-ID": request_id},
                timeout=30.0
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("text", text), data.get("llm_latency_ms", 0)
            else:
                logger.warning(f"LLM server returned {response.status_code}")
                return text, 0
    except Exception as e:
        logger.error(f"Failed to call LLM server: {e}")
        return text, 0

# ============== STT Engine ==============
class STTEngine:
    """STT 引擎管理器"""
    AVAILABLE_MODELS = {
        "qwen_asr": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "aligner_id": "Qwen/Qwen3-ForcedAligner-0.6B",
            "memory_gb": 3.5,
        },
        "qwen_asr_small": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "aligner_id": "Qwen/Qwen3-ForcedAligner-0.6B",
            "memory_gb": 1.5,
        },
    }

    def __init__(self, default_model: str = "qwen_asr"):
        self.default_model = default_model
        self.current_model_name = default_model
        self._model = None
        self._aligner = None
        self._is_loaded = False
        self._aligner_loaded = False
        self._loading = False
        self._load_lock = asyncio.Lock()
        self._model_info = self.AVAILABLE_MODELS.get(
            default_model, self.AVAILABLE_MODELS["qwen_asr"]
        )
        self.start_time = time.time()
        self.total_requests = 0
        self.failed_requests = 0
        self._active_connections = 0

    async def load(self, load_aligner: bool = False) -> bool:
        """加载模型"""
        async with self._load_lock:
            if self._is_loaded and (not load_aligner or self._aligner_loaded):
                return True

            if self._loading:
                logger.info("Model is loading, waiting...")
                while self._loading:
                    await asyncio.sleep(0.5)
                return self._is_loaded

            self._loading = True
            try:
                logger.info(f"Loading STT model: {self._model_info['model_id']}")
                loop = asyncio.get_event_loop()

                # 加载主模型
                if not self._is_loaded:
                    await loop.run_in_executor(None, self._load_model_sync)
                    self._is_loaded = True
                    logger.info("STT model loaded successfully")

                # 加载 ForcedAligner（如果需要时间戳功能）
                if load_aligner and not self._aligner_loaded:
                    logger.info(f"Loading ForcedAligner: {self._model_info['aligner_id']}")
                    await loop.run_in_executor(None, self._load_aligner_sync)
                    self._aligner_loaded = True
                    logger.info("ForcedAligner loaded successfully")

                return True
            except Exception as e:
                logger.error(f"Failed to load STT model: {e}", exc_info=True)
                self.failed_requests += 1
                return False
            finally:
                self._loading = False

    def _load_model_sync(self):
        """同步加载主模型"""
        import torch
        from qwen_asr import Qwen3ASRModel

        model_id = self._model_info["model_id"]

        # 检测设备
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

        # Qwen3-ASR 使用 bfloat16 效果更好
        dtype = torch.bfloat16 if device != "cpu" else torch.float32
        logger.info(f"Loading ASR model on {device} with {dtype}")

        self._model = Qwen3ASRModel.from_pretrained(
            model_id,
            dtype=dtype,
            device_map=device,
            max_new_tokens=256,
        )

    def _load_aligner_sync(self):
        """同步加载 ForcedAligner 模型"""
        import torch
        from qwen_asr import Qwen3ForcedAligner

        aligner_id = self._model_info.get("aligner_id", "Qwen/Qwen3-ForcedAligner-0.6B")

        # 检测设备
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

        # ForcedAligner 使用 bfloat16
        dtype = torch.bfloat16 if device != "cpu" else torch.float32
        logger.info(f"Loading ForcedAligner on {device} with {dtype}")

        self._aligner = Qwen3ForcedAligner.from_pretrained(
            aligner_id,
            dtype=dtype,
            device_map=device,
        )

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        return_timestamps: bool = False
    ) -> TranscriptionResult:
        """转写音频"""
        import numpy as np

        start_time = time.time()
        self.total_requests += 1

        try:
            # 确保模型已加载
            if not self._is_loaded:
                success = await self.load(load_aligner=return_timestamps)
                if not success:
                    raise RuntimeError("Failed to load STT model")

            # 如果需要时间戳但 aligner 未加载，尝试加载
            if return_timestamps and not self._aligner_loaded:
                success = await self.load(load_aligner=True)
                if not success:
                    logger.warning("Failed to load ForcedAligner, returning without timestamps")
                    return_timestamps = False

            # 转换音频
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            audio_array = audio_array.astype(np.float32) / 32768.0
            sample_rate = 16000

            # 执行转写
            loop = asyncio.get_event_loop()

            def _do_transcribe():
                lang = None if language == "auto" else language
                results = self._model.transcribe(
                    audio=(audio_array, sample_rate),
                    language=lang,
                )
                if results and len(results) > 0:
                    return results[0].text, results[0].language
                return "", language

            text, detected_lang = await loop.run_in_executor(None, _do_transcribe)
            text = text.strip()

            # 生成时间戳（如果需要）
            timestamps = None
            if return_timestamps and text and self._aligner_loaded:
                timestamps = await self._generate_timestamps(
                    audio_array, sample_rate, text, detected_lang or language
                )

            latency = (time.time() - start_time) * 1000
            return TranscriptionResult(
                text=text,
                confidence=1.0,
                language=detected_lang or language,
                is_final=True,
                stt_latency_ms=latency,
                model=self.current_model_name,
                timestamps=timestamps,
            )
        except Exception as e:
            self.failed_requests += 1
            logger.error(f"Transcription error: {e}", exc_info=True)
            raise

    async def _generate_timestamps(
        self,
        audio_array,
        sample_rate: int,
        text: str,
        language: str
    ) -> Optional[List[WordTimestamp]]:
        """使用 ForcedAligner 生成词级别时间戳"""
        import tempfile
        import os

        try:
            # 保存音频到临时文件
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
                # 使用 soundfile 写入音频
                import soundfile as sf
                sf.write(tmp_path, audio_array, sample_rate)

            loop = asyncio.get_event_loop()

            def _do_align():
                results = self._aligner.align(
                    audio=tmp_path,
                    text=text,
                    language=language if language != "auto" else "Chinese",
                )
                return results

            results = await loop.run_in_executor(None, _do_align)

            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except:
                pass

            # 转换结果格式
            if results and hasattr(results, 'segments'):
                timestamps = []
                for segment in results.segments:
                    for word_info in segment.get('words', []):
                        timestamps.append(WordTimestamp(
                            word=word_info.get('word', ''),
                            start=word_info.get('start', 0.0),
                            end=word_info.get('end', 0.0),
                        ))
                return timestamps if timestamps else None

            return None
        except Exception as e:
            logger.warning(f"Failed to generate timestamps: {e}")
            return None

    def is_loading(self) -> bool:
        return self._loading

    def is_model_loaded(self) -> bool:
        return self._is_loaded

    def is_aligner_loaded(self) -> bool:
        return self._aligner_loaded

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "active_connections": self._active_connections,
        }

    def increment_connections(self):
        self._active_connections += 1

    def decrement_connections(self):
        self._active_connections = max(0, self._active_connections - 1)

# ============== FastAPI App ==============
engine = STTEngine(default_model=STT_MODEL)

app = FastAPI(
    title="Voice Input Framework - STT Service",
    description="独立的语音识别服务，使用 Qwen3-ASR",
    version="1.1.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求ID中间件
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """为每个请求生成唯一ID"""
    request_id = str(uuid.uuid4())
    request_id_ctx.set(request_id)
    start_time = time.time()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # 记录请求指标
        duration = (time.time() - start_time) * 1000
        logger.info(
            f"{request.method} {request.url.path} - {response.status_code} - {duration:.2f}ms",
            extra={"request_id": request_id, "duration_ms": duration}
        )
        return response
    except Exception as e:
        logger.error(f"Request failed: {e}", extra={"request_id": request_id})
        raise

@app.on_event("startup")
async def startup_event():
    """启动时加载模型"""
    logger.info(f"Starting STT Service on {STT_HOST}:{STT_PORT}")
    logger.info(f"Default model: {STT_MODEL}")
    # 后台加载模型（非阻塞）
    asyncio.create_task(engine.load())

@app.on_event("shutdown")
async def shutdown_event():
    """关闭时清理"""
    logger.info("STT Service shutting down")

@app.get("/health", response_model=HealthStatus)
async def health_check():
    """健康检查"""
    return HealthStatus(
        status="ok" if engine.is_model_loaded() else "loading",
        version="1.1.0",
        uptime_seconds=time.time() - engine.start_time,
        current_model=engine.current_model_name,
        loaded_models=[engine.current_model_name] if engine.is_model_loaded() else [],
        active_connections=engine._active_connections,
        total_requests=engine.total_requests,
        failed_requests=engine.failed_requests,
    )

@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    """获取可用 STT 模型列表"""
    models = []
    for name, info in STTEngine.AVAILABLE_MODELS.items():
        models.append(ModelInfo(
            name=name,
            description=f"STT model: {info['model_id']}",
            is_loaded=(name == engine.current_model_name and engine.is_model_loaded()),
            is_default=(name == engine.default_model),
        ))
    return models

# ============== LLM 转发 API ==============

@app.get("/llm/models")
async def list_llm_models():
    """转发：获取可用 LLM 模型列表"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{LLM_SERVER_URL}/models", timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                # 包装成客户端期望的格式
                if isinstance(data, list):
                    return {"models": data}
                return data
            else:
                return {"error": f"LLM server returned {resp.status_code}"}
    except Exception as e:
        logger.error(f"Failed to get LLM models: {e}")
        return {"error": str(e)}

@app.post("/llm/models/select")
async def select_llm_model(request: Request):
    """转发：选择 LLM 模型"""
    try:
        body = await request.json()
        model_name = body.get("model_name", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{LLM_SERVER_URL}/models/select",
                data={"model_name": model_name},
                timeout=30.0
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"error": f"LLM server returned {resp.status_code}"}
    except Exception as e:
        logger.error(f"Failed to select LLM model: {e}")
        return {"error": str(e)}

@app.get("/llm/health")
async def llm_health():
    """转发：LLM 服务器健康检查"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=5.0)
            return resp.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/llm/enabled")
async def get_llm_enabled():
    """获取 LLM 后处理是否启用"""
    return {"enabled": LLM_ENABLED}

@app.put("/llm/enabled")
async def set_llm_enabled(request: Request):
    """设置 LLM 后处理是否启用"""
    global LLM_ENABLED
    body = await request.json()
    enabled = body.get("enabled", True)
    LLM_ENABLED = bool(enabled)
    return {"enabled": LLM_ENABLED}

# ============== LLM Prompt API ==============
@app.get("/llm/prompt")
async def get_llm_prompt():
    """转发：获取 LLM 提示词"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{LLM_SERVER_URL}/prompt", timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"LLM server returned {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.put("/llm/prompt")
async def update_llm_prompt(request: Request):
    """转发：更新 LLM 提示词"""
    try:
        body = await request.json()
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{LLM_SERVER_URL}/prompt",
                json=body,
                timeout=10.0
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"LLM server returned {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/transcribe", response_model=TranscriptionResult)
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    return_timestamps: bool = Form(False),
):
    """转写音频文件"""
    req_id = request_id_ctx.get()
    try:
        audio_content = await file.read()
        result = await engine.transcribe(
            audio_content,
            language=language,
            return_timestamps=return_timestamps
        )
        return result
    except Exception as e:
        logger.error(f"Transcription error: {e}", extra={"request_id": req_id})
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket 流式识别"""
    await websocket.accept()
    engine.increment_connections()
    logger.info("WebSocket connection accepted")

    # 获取 LLM 服务器状态
    llm_info = {"llm_enabled": LLM_ENABLED, "llm_model": None}
    try:
        async with httpx.AsyncClient() as client:
            llm_resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=5.0)
            if llm_resp.status_code == 200:
                llm_data = llm_resp.json()
                llm_info = {
                    "llm_enabled": LLM_ENABLED,
                    "llm_model": llm_data.get("current_model", "unknown")
                }
    except Exception as e:
        logger.debug(f"Failed to get LLM status: {e}")

    # 发送就绪消息
    await websocket.send_text(json.dumps({
        "type": "ready",
        "model": engine.current_model_name,
        "is_loading": engine.is_loading(),
        "aligner_loaded": engine.is_aligner_loaded(),
        "llm_enabled": llm_info["llm_enabled"],
        "llm_model": llm_info["llm_model"],
    }))

    audio_buffer = bytearray()
    return_timestamps = False

    try:
        while True:
            message = await asyncio.wait_for(websocket.receive_text(), timeout=120.0)
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "audio":
                audio_b64 = data.get("data", "")
                if audio_b64:
                    audio_chunk = base64.b64decode(audio_b64)
                    audio_buffer.extend(audio_chunk)

            elif msg_type == "config":
                # 处理配置消息
                return_timestamps = data.get("return_timestamps", False)
                language = data.get("language", "auto")
                await websocket.send_text(json.dumps({
                    "type": "config_ack",
                    "return_timestamps": return_timestamps,
                    "language": language,
                }))

            elif msg_type in ("end", "stop"):
                if audio_buffer:
                    try:
                        result = await asyncio.wait_for(
                            engine.transcribe(
                                bytes(audio_buffer),
                                return_timestamps=return_timestamps
                            ),
                            timeout=300.0
                        )
                        # 发送 STT 结果
                        await websocket.send_text(json.dumps({
                            "type": "stt_result",
                            "text": result.text,
                            "stt_latency_ms": result.stt_latency_ms,
                        }))
                        # 调用 LLM 服务器进行后处理
                        req_id = request_id_ctx.get()
                        if result.text.strip() and LLM_ENABLED:
                            # 发送 LLM 开始消息
                            await websocket.send_text(json.dumps({
                                "type": "llm_start",
                                "text": result.text[:50],
                            }))
                            logger.info(f"Calling LLM server for text ({len(result.text)} chars)")
                            # 调用 LLM 服务器
                            processed_text, llm_latency = await call_llm_server(result.text, req_id)
                            logger.info(f"LLM processing complete: {llm_latency:.1f}ms")
                        else:
                            processed_text = result.text
                            llm_latency = 0
                            if not LLM_ENABLED:
                                logger.info("LLM processing disabled, skipping")
                        # 发送最终结果
                        await websocket.send_text(json.dumps({
                            "type": "result",
                            "text": processed_text,
                            "confidence": result.confidence,
                            "language": result.language,
                            "is_final": True,
                            "stt_latency_ms": result.stt_latency_ms,
                            "llm_latency_ms": llm_latency,
                            "model": result.model,
                        }))
                        if result.timestamps:
                            timestamps_response = {
                                "type": "timestamps",
                                "timestamps": [
                                    {"word": ts.word, "start": ts.start, "end": ts.end}
                                    for ts in result.timestamps
                                ]
                            }
                            await websocket.send_text(json.dumps(timestamps_response))
                    except asyncio.TimeoutError:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "error_code": "E5002",
                            "error_message": "Transcription timeout",
                        }))

                await websocket.send_text(json.dumps({"type": "done"}))
                break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "error_code": "E5001",
                "error_message": str(e),
            }))
        except:
            pass
    finally:
        engine.decrement_connections()
        try:
            await websocket.close()
        except:
            pass

def main():
    """主函数"""
    logger.info(f"Starting STT Service on {STT_HOST}:{STT_PORT}")
    uvicorn.run(
        app,
        host=STT_HOST,
        port=STT_PORT,
        log_level=LOG_LEVEL.lower(),
    )

if __name__ == "__main__":
    main()
