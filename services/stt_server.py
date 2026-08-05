#!/usr/bin/env python3
"""
Voice Input Framework - STT Service
独立的 STT 服务器，使用 MLX 原生引擎进行语音识别。
运行在独立的 conda 环境: vif-stt (MLX + transformers 5.x)
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
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

import httpx

# 添加项目路径
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))
import uvicorn
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware

from services.diarize_engine import DIARIZE_ENABLED, DiarizationEngine
from services.stt_engine import (
    HealthStatus,
    ModelInfo,
    STTEngine,
    TranscriptionRequest,
    TranscriptionResult,
)
from shared.constants import DEFAULT_LLM_PORT, DEFAULT_STT_PORT
from shared.data_types import ErrorResponse
from shared.model_registry import MODELS_CONFIG, get_default_model

# ============== Configuration ==============
STT_HOST = os.getenv("VIF_STT_HOST", "0.0.0.0")
STT_PORT = int(os.getenv("VIF_STT_PORT", str(DEFAULT_STT_PORT)))
STT_MODEL = os.getenv(
    "VIF_STT_MODEL",
    get_default_model(),
)
LOG_LEVEL = os.getenv("VIF_LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT = float(os.getenv("VIF_REQUEST_TIMEOUT", "300.0"))
MAX_RETRIES = int(os.getenv("VIF_MAX_RETRIES", "3"))
RETRY_DELAY = float(os.getenv("VIF_RETRY_DELAY", "1.0"))

# LLM Server Configuration
# 默认用 127.0.0.1 而非 localhost:避免 Windows 上 IPv6(::1)优先解析导致连接超时回落延迟
LLM_SERVER_HOST = os.getenv("VIF_LLM_HOST", "127.0.0.1")
LLM_SERVER_PORT = int(os.getenv("VIF_LLM_PORT", str(DEFAULT_LLM_PORT)))
LLM_SERVER_URL = f"http://{LLM_SERVER_HOST}:{LLM_SERVER_PORT}"

# LLM Processing Toggle
LLM_ENABLED = os.getenv("VIF_LLM_ENABLED", "true").lower() == "true"
LLM_MODEL = os.getenv("VIF_LLM_MODEL", "Qwen3.5-4B-OptiQ")

# ============== State Persistence ==============
"""
持久化最后使用的 STT 模型和 LLM 开关状态，
避免服务器重启后需要重新设置。
"""
logger = logging.getLogger("stt-server")
STATE_DIR = Path.home() / ".config" / "voice-input-framework"
STATE_FILE = STATE_DIR / "stt_state.json"


def load_state() -> dict:
    """加载持久化的服务器状态"""
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load state file: {e}")
    return {}


def save_state(state: dict):
    """保存服务器状态到文件"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save state file: {e}")


# 从持久化状态恢复设置（仅在无环境变量覆盖时生效）
_persisted_state = load_state()
if "VIF_STT_MODEL" not in os.environ:
    if saved_model := _persisted_state.get("stt_model"):
        from shared.model_registry import MODELS_CONFIG

        if saved_model in MODELS_CONFIG:
            logger.info(f"Restoring STT model from saved state: {saved_model}")
            STT_MODEL = saved_model
        else:
            logger.warning(f"Saved model '{saved_model}' not available, using default")
if "VIF_LLM_ENABLED" not in os.environ:
    if "llm_enabled" in _persisted_state:
        LLM_ENABLED = bool(_persisted_state["llm_enabled"])
        logger.info(f"Restoring LLM enabled from saved state: {LLM_ENABLED}")
if "VIF_LLM_MODEL" not in os.environ:
    if saved_llm := _persisted_state.get("llm_model"):
        logger.info(f"Restoring LLM model from saved state: {saved_llm}")
        LLM_MODEL = saved_llm
        os.environ.setdefault("VIF_LLM_MODEL", saved_llm)

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


# ============== LLM Client ==============
async def call_llm_server(text: str, request_id: str = "") -> tuple[str, float]:
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
                timeout=30.0,
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


# ============== Diarization Engine ==============
diarize_engine = DiarizationEngine() if DIARIZE_ENABLED else None

# ============== FastAPI App ==============
engine = STTEngine(default_model=STT_MODEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:启动时后台加载模型,关闭时清理(FastAPI 推荐用法)"""
    logger.info(f"Starting STT Service on {STT_HOST}:{STT_PORT}")
    logger.info(f"Default model: {STT_MODEL}")
    # 后台加载模型（非阻塞）
    asyncio.create_task(engine.load())
    yield
    logger.info("STT Service shutting down")


app = FastAPI(
    title="Voice Input Framework - STT Service",
    description="独立的语音识别服务，使用 Qwen3-ASR",
    version="1.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
            extra={"request_id": request_id, "duration_ms": duration},
        )
        return response
    except Exception as e:
        logger.error(f"Request failed: {e}", extra={"request_id": request_id})
        raise


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
        diarize=diarize_engine.get_health() if diarize_engine else {"status": "disabled"},
    )


@app.get("/models", response_model=list[ModelInfo])
async def list_models():
    """获取可用 STT 模型列表"""
    models = []
    for name, info in STTEngine.AVAILABLE_MODELS.items():
        models.append(
            ModelInfo(
                name=name,
                description=f"STT model: {info['model_id']}",
                is_loaded=(name == engine.current_model_name and engine.is_model_loaded()),
                is_default=(name == engine.default_model),
            )
        )
    return models


# ============== LLM 转发 API ==============


def _llm_error(message: str) -> dict:
    """构造结构化 LLM 转发错误响应(M7:统一错误模型)"""
    return ErrorResponse(
        error_code="LLM_PROXY_ERROR",
        error_message=message,
    ).to_dict()


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
                return _llm_error(f"LLM server returned {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to get LLM models: {e}")
        return _llm_error(str(e))


@app.post("/llm/models/select")
async def select_llm_model(request: Request):
    """转发：选择 LLM 模型"""
    try:
        body = await request.json()
        model_name = body.get("model_name", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{LLM_SERVER_URL}/models/select", data={"model_name": model_name}, timeout=30.0
            )
            if resp.status_code == 200:
                # 持久化 LLM 模型选择
                state = load_state()
                state["llm_model"] = model_name
                save_state(state)
                logger.info(f"LLM model saved to state: {model_name}")
                return resp.json()
            else:
                return _llm_error(f"LLM server returned {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to select LLM model: {e}")
        return _llm_error(str(e))


@app.get("/llm/health")
async def llm_health():
    """转发：LLM 服务器健康检查"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=5.0)
            return resp.json()
    except Exception as e:
        logger.error(f"LLM health check failed: {e}")
        return _llm_error(str(e))


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
    # 持久化
    state = load_state()
    state["llm_enabled"] = LLM_ENABLED
    save_state(state)
    logger.info(f"LLM enabled set to {LLM_ENABLED} (persisted)")
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
            return _llm_error(f"LLM server returned {resp.status_code}")
    except Exception as e:
        return _llm_error(str(e))


@app.put("/llm/prompt")
async def update_llm_prompt(request: Request):
    """转发：更新 LLM 提示词"""
    try:
        body = await request.json()
        async with httpx.AsyncClient() as client:
            resp = await client.put(f"{LLM_SERVER_URL}/prompt", json=body, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            return _llm_error(f"LLM server returned {resp.status_code}")
    except Exception as e:
        return _llm_error(str(e))


@app.post("/models/select")
async def select_stt_model(model_name: str = Form(...)):
    """切换 STT 模型（立即返回，后台加载）"""
    try:
        logger.info(f"Switching STT model to: {model_name}")
        result = await engine.switch_model(model_name)
        # 持久化 STT 模型选择
        state = load_state()
        state["stt_model"] = model_name
        save_state(state)
        logger.info(f"STT model saved to state: {model_name}")
        return result
    except ValueError as e:
        logger.error(f"ValueError switching model: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error switching model: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e!s}")


@app.get("/models/status/{model_name}")
async def get_model_status(model_name: str):
    """获取指定模型的加载状态"""
    if model_name not in engine.AVAILABLE_MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_name}")

    is_current = model_name == engine.current_model_name
    is_loaded = is_current and engine.is_model_loaded()
    is_loading = is_current and engine.is_loading()

    return {
        "name": model_name,
        "is_current": is_current,
        "is_loaded": is_loaded,
        "is_loading": is_loading,
        "model_info": engine.AVAILABLE_MODELS.get(model_name, {}),
    }


@app.post("/transcribe", response_model=TranscriptionResult)
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    """转写音频文件"""
    req_id = request_id_ctx.get()
    try:
        audio_content = await file.read()
        result = await engine.transcribe(
            audio_content,
            language=language,
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

    async def _safe_send(payload: dict) -> bool:
        """发送消息;客户端已断开时返回 False(不抛异常)"""
        try:
            await websocket.send_text(json.dumps(payload))
            return True
        except (RuntimeError, WebSocketDisconnect, Exception):  # noqa: BLE001
            return False

    # 获取 LLM 服务器状态
    llm_info = {"llm_enabled": LLM_ENABLED, "llm_model": None}
    try:
        async with httpx.AsyncClient() as client:
            llm_resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=5.0)
            if llm_resp.status_code == 200:
                llm_data = llm_resp.json()
                llm_info = {
                    "llm_enabled": LLM_ENABLED,
                    "llm_model": llm_data.get("current_model", "unknown"),
                }
    except Exception as e:
        logger.debug(f"Failed to get LLM status: {e}")

    # 发送就绪消息
    await _safe_send(
        {
            "type": "ready",
            "model": engine.current_model_name,
            "is_loading": engine.is_loading(),
            "llm_enabled": llm_info["llm_enabled"],
            "llm_model": llm_info["llm_model"],
        }
    )

    audio_queue = asyncio.Queue()
    stream_error = None
    language = "auto"

    # ── 异步生成器：从 queue 读取音频块供 transcribe_stream ──
    async def audio_stream_generator():
        nonlocal stream_error
        try:
            while True:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=300.0)
                if chunk is None:  # 结束标记
                    break
                yield chunk
        except TimeoutError:
            stream_error = "Audio receive timeout"
        except Exception as e:
            stream_error = str(e)

    # ── 接收循环（投递到 queue）──
    async def receive_loop():
        nonlocal stream_error, language
        try:
            while True:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=120.0)
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "audio":
                    audio_b64 = data.get("data", "")
                    if audio_b64:
                        await audio_queue.put(base64.b64decode(audio_b64))

                elif msg_type == "config":
                    language = data.get("language", "auto")
                    await _safe_send(
                        {
                            "type": "config_ack",
                            "language": language,
                        }
                    )

                elif msg_type in ("end", "stop"):
                    await audio_queue.put(None)  # 通知 stream 结束
                    break
        except TimeoutError:
            stream_error = "WebSocket receive timeout"
        except WebSocketDisconnect:
            await audio_queue.put(None)
        except Exception as e:
            stream_error = str(e)
            await audio_queue.put(None)

    # ── 启动接收循环 ──
    receive_task = asyncio.create_task(receive_loop())

    # ── 等待音频接收完成，一次性转写 ──
    await receive_task

    # 收集所有音频数据
    all_audio = bytearray()
    while not audio_queue.empty():
        chunk = audio_queue.get_nowait()
        if chunk is not None:
            all_audio.extend(chunk)

    if all_audio:
        error_sent = False
        try:
            result = await asyncio.wait_for(
                engine.transcribe(
                    bytes(all_audio),
                    language=language,
                ),
                timeout=600.0,
            )

            # 发送 STT 结果
            await _safe_send(
                {
                    "type": "stt_result",
                    "text": result.text,
                    "stt_latency_ms": result.stt_latency_ms,
                    "confidence": result.confidence,
                    "language": result.language,
                    "model": result.model,
                }
            )

            # LLM 后处理
            if result.text.strip() and LLM_ENABLED:
                await _safe_send(
                    {
                        "type": "llm_start",
                        "text": result.text[:50],
                    }
                )
                processed_text, llm_latency = await call_llm_server(result.text)
            else:
                processed_text = result.text
                llm_latency = 0

            await _safe_send(
                {
                    "type": "result",
                    "text": processed_text,
                    "confidence": result.confidence,
                    "language": result.language,
                    "is_final": True,
                    "stt_latency_ms": result.stt_latency_ms,
                    "llm_latency_ms": llm_latency,
                    "model": result.model,
                }
            )

        except TimeoutError:
            error_sent = True
            await _safe_send(
                {
                    "type": "error",
                    "error_code": "E5002",
                    "error_message": "转写超时",
                }
            )
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            error_sent = True
            await _safe_send(
                {
                    "type": "error",
                    "error_code": "E5001",
                    "error_message": str(e),
                }
            )

    # 已发送 error 视为终态,不再发 done(避免客户端断开后 send 报错);
    # all_audio 为空时也照常发 done(与旧行为一致)
    if not (all_audio and error_sent):
        await _safe_send({"type": "done"})

    try:
        await websocket.close()
    except:
        pass
    engine.decrement_connections()


# ============== Diarization API ==============


@app.get("/diarize/models")
async def list_diarize_models():
    """获取可用说话人分离模型"""
    if not diarize_engine:
        return {"status": "disabled", "message": "Diarization disabled (VIF_DIARIZE_ENABLED=false)"}
    return {
        "models": [
            {
                "name": diarize_engine.model_id,
                "description": "Pyannote Speaker Diarization 3.1",
                "is_loaded": diarize_engine.is_loaded,
                "is_loading": diarize_engine.is_loading,
                "device": diarize_engine.device,
            }
        ],
        "default": diarize_engine.model_id,
    }


@app.post("/diarize")
async def diarize(
    file: UploadFile = File(...),
    num_speakers: int | None = Form(None),
    min_speakers: int | None = Form(None),
    max_speakers: int | None = Form(None),
):
    """对上传的音频文件进行说话人分离"""
    if not diarize_engine:
        raise HTTPException(status_code=503, detail="Diarization disabled")

    req_id = request_id_ctx.get()
    import tempfile

    import aiofiles

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        content = await file.read()
        async with aiofiles.open(tmp_path, "wb") as f:
            await f.write(content)

        logger.info(
            f"Starting diarization: {file.filename} ({len(content)} bytes)",
            extra={"request_id": req_id},
        )

        result = await diarize_engine.diarize(
            audio_path=tmp_path,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )

        result["filename"] = file.filename
        result["file_size"] = len(content)

        logger.info(
            f"Diarization complete: {result['num_speakers']} speakers, "
            f"{result['duration']:.0f}s, {result['inference_latency_ms']:.0f}ms",
            extra={"request_id": req_id},
        )

        return result

    except ImportError as e:
        raise HTTPException(
            status_code=501,
            detail=f"pyannote.audio not installed: {e}. Run: pip install pyannote.audio==3.3.3",
        )
    except Exception as e:
        logger.error(f"Diarization error: {e}", exc_info=True, extra={"request_id": req_id})
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def main():
    """主函数"""
    from shared.version_check import check_python_version

    check_python_version()
    logger.info(f"Starting STT Service on {STT_HOST}:{STT_PORT}")
    uvicorn.run(
        app,
        host=STT_HOST,
        port=STT_PORT,
        log_level=LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
