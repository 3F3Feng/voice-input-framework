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
from fastapi.responses import JSONResponse

from services.diarize_engine import DIARIZE_ENABLED, DiarizationEngine
from services import model_catalog, vocabulary
from services.audio_io import UnsupportedAudio, decode_to_pcm16k
from services.stt_engine import (
    HealthStatus,
    ModelInfo,
    STTEngine,
    TranscriptionRequest,
    TranscriptionResult,
)
from shared import auth, i18n, llm_backend
from shared.constants import (
    DEFAULT_BIND_HOST,
    DEFAULT_CORS_ORIGINS,
    DEFAULT_LLM_PORT,
    DEFAULT_STT_PORT,
    MAX_UPLOAD_SIZE,
    WS_MAX_MESSAGE_SIZE,
)
from shared.data_types import ErrorResponse
from shared.model_registry import IS_APPLE_SILICON, MODELS_CONFIG, get_default_model

# ============== Configuration ==============
# 默认只绑定回环地址:本服务无鉴权,不应默认暴露到局域网。
STT_HOST = os.getenv("VIF_STT_HOST", DEFAULT_BIND_HOST)
STT_PORT = int(os.getenv("VIF_STT_PORT", str(DEFAULT_STT_PORT)))
CORS_ORIGINS = [
    o.strip() for o in os.getenv("VIF_CORS_ORIGINS", "").split(",") if o.strip()
] or DEFAULT_CORS_ORIGINS


def _resolve_stt_model() -> str:
    """定这次用哪个 STT 模型。

    优先级:`VIF_STT_MODEL` > 按硬件推荐 > 静态兜底。

    「按硬件推荐」会量后端、核数、内存和显存 —— 默认值配不上机器,用户开箱
    看到的要么是慢得不能用,要么直接 OOM。实测在 6 核 i5 / 8GB 上,
    whisper_small 是 2.31x 实时(说 10 秒等 23 秒),而 whisper_base 是 0.70x;
    同一份代码在有独显的机器上就该自动用更大的模型,不该一刀切。
    """
    # 模块级的 logger 在本函数被调用之后才定义,这里自己取一个同名的。
    log = logging.getLogger("stt-server")
    explicit = os.getenv("VIF_STT_MODEL")
    if explicit:
        return explicit
    try:
        from services.device import profile, recommend_stt_model

        model, why = recommend_stt_model(profile())
        log.info(f"按硬件选定 STT 模型: {model}({why});可用 VIF_STT_MODEL 覆盖")
        return model
    except Exception as e:  # noqa: BLE001 - 探测失败不该让服务起不来
        fallback = get_default_model()
        log.warning(f"硬件探测失败({e}),退回默认模型 {fallback}")
        return fallback


STT_MODEL = _resolve_stt_model()
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


def _llm_support() -> tuple[bool, str | None]:
    """这台 STT 服务背后的 LLM 后处理能不能用,不能用时给出原因。

    Apple Silicon 用 MLX,其它平台用 llama.cpp(要 `setup-env --llm` 装上);
    判断规则和 LLM 服务挑后端用的是同一个函数(shared/llm_backend.py),两边不会
    一个说能用、一个加载失败。以前在不支持的机器上照样能打开开关:LLM 服务起来
    就加载失败,界面等满 30 秒后说「还在加载模型」;而开关的默认值又是开,每句话
    都白走一趟反代。LLM 服务配在别的机器上(`VIF_LLM_HOST` 不是本机)时,能不能跑
    由那台机器决定,这里不拦。
    """
    if LLM_SERVER_HOST not in ("127.0.0.1", "localhost", "::1"):
        return True, None
    _backend, reason = llm_backend.choose_backend(IS_APPLE_SILICON, llm_backend.requested_backend())
    return reason is None, reason


LLM_SUPPORTED, LLM_UNSUPPORTED_REASON = _llm_support()


def llm_active() -> bool:
    """这句话要不要走 LLM 后处理:开关开着,而且这台机器能跑。"""
    return LLM_ENABLED and LLM_SUPPORTED


def _llm_status(lang: str = i18n.ZH) -> dict:
    return {
        "enabled": llm_active(),
        "supported": LLM_SUPPORTED,
        "reason": None if LLM_SUPPORTED else i18n.localize(lang, LLM_UNSUPPORTED_REASON),
    }


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
LLM_PROCESS_TIMEOUT = 30.0


def _llm_client(lang: str = i18n.ZH) -> httpx.AsyncClient:
    """转发给 LLM 服务用的客户端:带上令牌,也带上客户端的界面语言,
    LLM 服务回的提示(加载失败、结果不能用的原因)就和界面是同一种语言。"""
    return httpx.AsyncClient(headers={**auth.outgoing_headers(), **i18n.header(lang)})


async def call_llm_server(
    text: str,
    request_id: str = "",
    vocabulary_hint: str | None = None,
    lang: str = i18n.ZH,
) -> tuple[str, float, str | None]:
    """调用 LLM 服务器进行后处理。

    失败时文本退回原文——这一点不变,宁可给原文也不能什么都不给。变的是
    **失败要说出来**:以前这里静默返回原文,客户端无从知道「这次没有经过后处理」,
    用户看到一段没加标点、满是「那个」的文字,还以为 LLM 就这水平。

    Returns:
        tuple: (processed_text, latency_ms, llm_error)。llm_error 为 None 表示
        后处理成功;否则是一句给用户看的原因,此时 processed_text 就是原文。
    """
    t = i18n.t
    try:
        async with _llm_client(lang) as client:
            response = await client.post(
                f"{LLM_SERVER_URL}/process",
                json={
                    "text": text,
                    "options": {"vocabulary_hint": vocabulary_hint} if vocabulary_hint else {},
                },
                headers={"X-Request-ID": request_id},
                timeout=LLM_PROCESS_TIMEOUT,
            )
    except httpx.TimeoutException:
        logger.error("LLM server timed out")
        secs = int(LLM_PROCESS_TIMEOUT)
        return (
            text,
            0,
            t(lang, f"LLM 服务 {secs} 秒没有应答", f"The LLM service did not respond in {secs}s"),
        )
    except httpx.ConnectError as e:
        logger.error(f"Failed to connect to LLM server: {e}")
        return (
            text,
            0,
            t(
                lang,
                "连不上 LLM 服务(可能没有启动)",
                "Cannot reach the LLM service (it may not be running)",
            ),
        )
    except Exception as e:
        logger.error(f"Failed to call LLM server: {e}")
        return text, 0, t(lang, f"调用 LLM 服务失败:{e}", f"Calling the LLM service failed: {e}")

    if response.status_code != 200:
        logger.warning(f"LLM server returned {response.status_code}")
        return text, 0, _upstream_message(response)
    try:
        data = response.json()
    except ValueError:
        return (
            text,
            0,
            t(
                lang,
                "LLM 服务返回了无法解析的内容",
                "The LLM service returned a response that could not be parsed",
            ),
        )
    latency = data.get("llm_latency_ms", 0) or 0
    # LLM 服务自己判定结果不能用(截断、答非所问、空结果、模型出错)时回
    # 200 + success=False + error,text 就是原文。
    if data.get("success") is False:
        reason = data.get("error") or t(
            lang, "LLM 没能处理这段文字", "The LLM could not process this text"
        )
        logger.warning(f"LLM post-processing rejected: {reason}")
        return data.get("text") or text, latency, reason
    return data.get("text", text), latency, None


# ============== LLM 模型名缓存(R29)==============
#
# WS 的 ready 消息要带 LLM 当前模型名,纯粹是给客户端看的信息。以前每条 WS
# 连接(也就是每句话)都先同步问一次 LLM 的 /health 才发 ready:多一次往返,
# LLM 卡住时要多等满 5 秒超时。现在只用缓存,过期了就在后台刷新,绝不挡在
# 转写前面;刚启动还没问到时报 None,和以前 LLM 不可达时一样。
LLM_MODEL_CACHE_TTL = 30.0
_llm_model_cache: dict = {"model": None, "at": float("-inf"), "refreshing": False}


async def _refresh_llm_model() -> None:
    try:
        async with httpx.AsyncClient(headers=auth.outgoing_headers()) as client:
            resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=5.0)
            if resp.status_code == 200:
                _llm_model_cache["model"] = resp.json().get("current_model")
            else:
                _llm_model_cache["model"] = None
    except Exception as e:
        logger.debug(f"Failed to get LLM status: {e}")
        _llm_model_cache["model"] = None
    finally:
        _llm_model_cache["at"] = time.monotonic()
        _llm_model_cache["refreshing"] = False


def _cached_llm_model() -> str | None:
    """LLM 当前模型名(可能稍旧)。过期就起一个后台刷新,本次照样立刻返回。"""
    stale = time.monotonic() - _llm_model_cache["at"] > LLM_MODEL_CACHE_TTL
    if stale and not _llm_model_cache["refreshing"]:
        _llm_model_cache["refreshing"] = True
        asyncio.get_running_loop().create_task(_refresh_llm_model())
    return _llm_model_cache["model"]


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
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_token_middleware(request: Request, call_next):
    """可选的访问令牌(F20,见 shared/auth.py)。没设 VIF_API_TOKEN 时什么都不做。"""
    if auth.needs_check(request.method, request.url.path) and not auth.token_ok(
        request.headers.get("authorization"), request.query_params.get("token")
    ):
        return JSONResponse(
            status_code=401,
            content={
                "error_code": "UNAUTHORIZED",
                "error_message": auth.unauthorized_message(i18n.lang_of(request)),
            },
        )
    return await call_next(request)


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
async def health_check(request: Request):
    """健康检查"""
    lang = i18n.lang_of(request)
    load_error = i18n.localize(lang, engine.load_error())
    if engine.is_model_loaded():
        status = "ok"
    elif load_error:
        status = "error"
    else:
        status = "loading"
    return HealthStatus(
        status=status,
        version="1.1.0",
        uptime_seconds=time.time() - engine.start_time,
        current_model=engine.current_model_name,
        loaded_models=[engine.current_model_name] if engine.is_model_loaded() else [],
        active_connections=engine._active_connections,
        total_requests=engine.total_requests,
        failed_requests=engine.failed_requests,
        diarize=diarize_engine.get_health() if diarize_engine else {"status": "disabled"},
        # 实际选中的后端与机器画像。用户(和我们)得能一眼看出这台机器到底
        # 跑在 GPU 上还是 CPU 上、为什么给了这个模型 —— 以前这些全靠猜。
        hardware=engine.backend_info(lang)
        or {"status": i18n.t(lang, "模型尚未加载", "Model not loaded yet")},
        error=load_error,
        loading=engine.loading_progress(),
    )


@app.get("/models", response_model=list[ModelInfo])
async def list_models(request: Request):
    """获取可用 STT 模型列表"""
    lang = i18n.lang_of(request)
    models = []
    for name in STTEngine.AVAILABLE_MODELS:
        models.append(
            ModelInfo(
                name=name,
                is_loaded=(name == engine.current_model_name and engine.is_model_loaded()),
                is_default=(name == engine.default_model),
                **model_catalog.describe(name, lang),
            )
        )
    return models


# ============== LLM 转发 API ==============


def _llm_error(message: str, status_code: int = 502) -> JSONResponse:
    """构造结构化 LLM 转发错误响应(M7:统一错误模型)。

    必须带非 2xx 状态码。以前这里返回一个裸 dict,FastAPI 照样按 200 发出去,
    只看状态码的调用方(Rust 客户端、client/network.py)就把转发失败当成了成功
    —— LLM 模型切换失败一路传到界面上会弹成「已切换」。
    """
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error_code="LLM_PROXY_ERROR",
            error_message=message,
        ).to_dict(),
    )


def _upstream_message(resp: httpx.Response) -> str:
    """从上游(LLM 服务)的失败响应里挖出可读的原因,挖不到就退回状态码。"""
    try:
        data = resp.json()
        if isinstance(data, dict):
            for key in ("message", "error_message", "detail"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
    except Exception:
        pass
    return f"LLM server returned {resp.status_code}"


@app.get("/llm/models")
async def list_llm_models(request: Request):
    """转发：获取可用 LLM 模型列表"""
    lang = i18n.lang_of(request)
    try:
        async with _llm_client(lang) as client:
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
    lang = i18n.lang_of(request)
    t = i18n.t
    try:
        body = await request.json()
        model_name = body.get("model_name", "")
        async with _llm_client(lang) as client:
            resp = await client.post(
                f"{LLM_SERVER_URL}/models/select", data={"model_name": model_name}, timeout=30.0
            )
            if resp.status_code != 200:
                logger.error(f"LLM model switch failed: {model_name} ({resp.status_code})")
                why = _upstream_message(resp)
                return _llm_error(
                    t(lang, f"LLM 模型切换失败:{why}", f"Switching the LLM model failed: {why}"),
                    resp.status_code,
                )
            data = resp.json()
            # 老版本 LLM 服务用 200 + status:"failed" 报失败,这里同样按失败处理,
            # 否则下面会把一个根本没加载成功的模型持久化下来。
            if isinstance(data, dict) and data.get("status") == "failed":
                logger.error(f"LLM model switch failed: {model_name}")
                why = data.get("message") or model_name
                return _llm_error(
                    t(lang, f"LLM 模型切换失败:{why}", f"Switching the LLM model failed: {why}"),
                    502,
                )
            # 持久化 LLM 模型选择(只有确实切成功了才存)
            state = load_state()
            state["llm_model"] = model_name
            save_state(state)
            logger.info(f"LLM model saved to state: {model_name}")
            return data
    except httpx.TimeoutException:
        # 下载一个 4B 模型常常超过 30 秒。以前这里报成一句泛泛的「切换失败」,
        # 而 LLM 服务其实还在后台接着加载,最后换成功了——用户却以为没换成。
        # LLM 服务加载成功后会自己记下选择(llm_state.json),所以直说「还在加载」。
        logger.warning(f"LLM model switch still loading after timeout: {model_name}")
        # 客户端(gui/src/App.vue 的 switchLlm)按「还在加载」/「still loading」认出这种情况。
        return _llm_error(
            t(
                lang,
                f"LLM 还在加载 {model_name},完成后自动生效",
                f"LLM is still loading {model_name}; it will take effect once loaded",
            ),
            504,
        )
    except Exception as e:
        logger.error(f"Failed to select LLM model: {e}")
        return _llm_error(str(e))


@app.get("/llm/health")
async def llm_health(request: Request):
    """转发：LLM 服务器健康检查"""
    lang = i18n.lang_of(request)
    try:
        async with _llm_client(lang) as client:
            resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=5.0)
            return resp.json()
    except Exception as e:
        logger.error(f"LLM health check failed: {e}")
        return _llm_error(str(e))


@app.get("/llm/enabled")
async def get_llm_enabled(request: Request):
    """获取 LLM 后处理是否启用"""
    # `supported` / `reason`:不支持的平台上客户端把开关置灰并说明原因(F17)。
    # `enabled` 报的是实际生效的值——不支持时永远是 false,开关才不会显示成开着。
    return _llm_status(i18n.lang_of(request))


@app.put("/llm/enabled")
async def set_llm_enabled(request: Request):
    """设置 LLM 后处理是否启用"""
    global LLM_ENABLED
    lang = i18n.lang_of(request)
    body = await request.json()
    enabled = bool(body.get("enabled", True))
    if enabled and not LLM_SUPPORTED:
        # 409:请求本身没问题,是这台机器的状态不允许。不持久化,免得换到能跑
        # 的配置之后莫名其妙自己开了。
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                error_code="LLM_UNSUPPORTED",
                error_message=i18n.localize(lang, LLM_UNSUPPORTED_REASON)
                or i18n.t(
                    lang,
                    "这台机器不支持 LLM 后处理",
                    "LLM post-processing is not supported on this machine",
                ),
            ).to_dict(),
        )
    LLM_ENABLED = enabled
    # 持久化
    state = load_state()
    state["llm_enabled"] = LLM_ENABLED
    save_state(state)
    logger.info(f"LLM enabled set to {LLM_ENABLED} (persisted)")
    return _llm_status(lang)


# ============== LLM Prompt API ==============
@app.get("/llm/prompt")
async def get_llm_prompt(request: Request):
    """转发：获取 LLM 提示词"""
    lang = i18n.lang_of(request)
    try:
        async with _llm_client(lang) as client:
            resp = await client.get(f"{LLM_SERVER_URL}/prompt", timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
            why = _upstream_message(resp)
            return _llm_error(
                i18n.t(lang, f"读取提示词失败:{why}", f"Failed to read the prompt: {why}"),
                resp.status_code,
            )
    except Exception as e:
        return _llm_error(str(e))


@app.put("/llm/prompt")
async def update_llm_prompt(request: Request):
    """转发：更新 LLM 提示词"""
    lang = i18n.lang_of(request)
    try:
        body = await request.json()
        async with _llm_client(lang) as client:
            resp = await client.put(f"{LLM_SERVER_URL}/prompt", json=body, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            why = _upstream_message(resp)
            return _llm_error(
                i18n.t(lang, f"保存提示词失败:{why}", f"Failed to save the prompt: {why}"),
                resp.status_code,
            )
    except Exception as e:
        return _llm_error(str(e))


# ============== 个人词库 ==============
#: 当前词库(services/vocabulary.py 解析后的)。原始的若干行存在 stt_state.json 里。
VOCABULARY = vocabulary.parse(load_state().get("vocabulary", []))


@app.get("/vocabulary")
async def get_vocabulary():
    """个人词库:原始的若干行,以及解析出的热词 / 替换规则数(给界面显示)。"""
    entries = load_state().get("vocabulary", [])
    return {
        "entries": entries,
        "hotwords": len(VOCABULARY.hotwords),
        "rules": len(VOCABULARY.rules),
    }


@app.put("/vocabulary")
async def set_vocabulary(request: Request):
    """保存个人词库。一行一条:`石枫` 是热词,`陶睿 => Tauri` 是替换规则。"""
    global VOCABULARY
    body = await request.json()
    entries = body.get("entries", [])
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        raise HTTPException(
            status_code=400,
            detail=i18n.t(
                i18n.lang_of(request),
                "entries 必须是字符串列表",
                "entries must be a list of strings",
            ),
        )
    entries = [e.strip() for e in entries if e.strip()][: vocabulary.MAX_ENTRIES]
    VOCABULARY = vocabulary.parse(entries)
    state = load_state()
    state["vocabulary"] = entries
    save_state(state)
    logger.info(
        f"Vocabulary saved: {len(VOCABULARY.hotwords)} hotwords, {len(VOCABULARY.rules)} rules"
    )
    return {
        "entries": entries,
        "hotwords": len(VOCABULARY.hotwords),
        "rules": len(VOCABULARY.rules),
    }


@app.delete("/llm/prompt")
async def reset_llm_prompt(request: Request):
    """转发:恢复默认 LLM 提示词"""
    lang = i18n.lang_of(request)
    try:
        async with _llm_client(lang) as client:
            resp = await client.delete(f"{LLM_SERVER_URL}/prompt", timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            why = _upstream_message(resp)
            return _llm_error(
                i18n.t(
                    lang,
                    f"恢复默认提示词失败:{why}",
                    f"Failed to restore the default prompt: {why}",
                ),
                resp.status_code,
            )
    except Exception as e:
        return _llm_error(str(e))


@app.post("/models/select")
async def select_stt_model(model_name: str = Form(...)):
    """切换 STT 模型（立即返回，后台加载）"""

    def persist(name: str) -> None:
        # 只在真正加载成功之后才记下来。以前在加载之前就写,切到一个坏模型,
        # 重启服务还会继续加载它,用户只能手动改状态文件。
        state = load_state()
        state["stt_model"] = name
        save_state(state)
        logger.info(f"STT model saved to state: {name}")

    try:
        logger.info(f"Switching STT model to: {model_name}")
        return await engine.switch_model(model_name, on_loaded=persist)
    except ValueError as e:
        logger.error(f"ValueError switching model: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Unexpected error switching model: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e!s}") from e


@app.get("/models/status/{model_name}")
async def get_model_status(model_name: str, request: Request):
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
        # 切换失败的原因(会回退到切换前的模型,所以不能只看 /health 的 error)
        "error": i18n.localize(i18n.lang_of(request), engine.switch_error(model_name)),
        "loading": engine.loading_progress() if is_loading else None,
        "model_info": engine.AVAILABLE_MODELS.get(model_name, {}),
    }


UPLOAD_CHUNK_SIZE = 1024 * 1024


async def _read_capped(file: UploadFile, limit: int = MAX_UPLOAD_SIZE) -> bytes:
    """分块读上传体,累计超过 limit 立刻 413。

    不要写成 `content = await file.read()` 再比大小:那一行返回的时候整个 body
    已经变成一个 bytes 对象躺在内存里了,后面再判上限已经晚了——上限存在的目的
    就是不让这件事发生。边读边累计的话,最多只多读一个 chunk 就能停手。
    WebSocket 那条路径(`receive_loop` 里的 `received_bytes`)一直就是这么做的。

    能管到的是内存。Starlette 在调到这个 handler 之前就已经把 multipart body
    落到临时文件里了,那部分磁盘占用要拦得靠 ASGI 中间件,不在这里。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Audio too large: exceeds {limit} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/transcribe", response_model=TranscriptionResult)
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    """转写音频文件"""
    req_id = request_id_ctx.get()
    lang = i18n.lang_of(request)
    try:
        try:
            audio_content = decode_to_pcm16k(await _read_capped(file))
        except UnsupportedAudio as e:
            raise HTTPException(status_code=415, detail=i18n.exc_text(lang, e)) from e
        result = await engine.transcribe(
            audio_content,
            language=language,
            context=vocabulary.context_text(VOCABULARY),
        )
        result.text = vocabulary.apply_rules(result.text, VOCABULARY)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription error: {e}", extra={"request_id": req_id})
        raise HTTPException(status_code=500, detail=i18n.exc_text(lang, e))


#: 转写 / 后处理期间每隔多久给客户端发一次 `progress`。
KEEPALIVE_INTERVAL_S = 5.0


async def _with_keepalive(coro, stage: str, send):
    """跑 `coro`,期间每 `KEEPALIVE_INTERVAL_S` 秒发一条 `{"type": "progress"}`。

    客户端以前对每条消息只能干等一个固定上限(5 分钟):设短了,CPU 上转写长录音
    会被误判超时;设长了,服务端真挂了要等 5 分钟才知道。有了心跳,客户端就能在
    「一直有进展」时一直等、在「半天没动静」时很快放弃。老客户端不认 progress,
    会直接忽略,协议向后兼容。
    """
    task = asyncio.ensure_future(coro)
    started = time.time()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=KEEPALIVE_INTERVAL_S)
            if done:
                return task.result()
            await send(
                {"type": "progress", "stage": stage, "elapsed_s": round(time.time() - started, 1)}
            )
    finally:
        if not task.done():
            task.cancel()


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket 流式识别"""
    # 令牌(F20)。HTTP 中间件管不到 WebSocket,这里单独查;不对就拒绝握手。
    if not auth.token_ok(
        websocket.headers.get("authorization"), websocket.query_params.get("token")
    ):
        await websocket.close(code=4401, reason="unauthorized")
        return
    # 界面语言(F22):握手时的 Accept-Language,这条连接上的提示都用它。
    lang = i18n.lang_of(websocket)
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

    # 发送就绪消息。`llm_model` 用缓存的值,见 `_cached_llm_model`:以前这里每次
    # 都先同步问一次 LLM 的 /health(5 秒超时)才发 ready——每句话多一次往返,
    # LLM 卡住时说完话要多等 5 秒。
    llm_enabled = llm_active()
    await _safe_send(
        {
            "type": "ready",
            "model": engine.current_model_name,
            "is_loading": engine.is_loading(),
            "llm_enabled": llm_enabled,
            "llm_model": _cached_llm_model() if llm_enabled else None,
        }
    )

    audio_queue = asyncio.Queue()
    stream_error = None
    language = "auto"
    received_bytes = 0
    size_error = None

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
        nonlocal stream_error, language, received_bytes, size_error
        try:
            while True:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=120.0)
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "audio":
                    audio_b64 = data.get("data", "")
                    if audio_b64:
                        chunk = base64.b64decode(audio_b64)
                        # 单帧上限:拒绝超大帧,避免单条消息撑爆内存
                        if len(chunk) > WS_MAX_MESSAGE_SIZE:
                            size_error = (
                                f"Audio frame too large: {len(chunk)} > {WS_MAX_MESSAGE_SIZE} bytes"
                            )
                            await audio_queue.put(None)
                            break
                        # 累计上限:一次会话的音频总量不得超过上传上限
                        received_bytes += len(chunk)
                        if received_bytes > MAX_UPLOAD_SIZE:
                            size_error = (
                                f"Audio stream too large: {received_bytes} > "
                                f"{MAX_UPLOAD_SIZE} bytes"
                            )
                            await audio_queue.put(None)
                            break
                        await audio_queue.put(chunk)

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

    # 超出体积上限:丢弃已收音频并直接返回错误(终态,不再发 done)
    if size_error:
        logger.warning(f"Rejecting oversized audio stream: {size_error}")
        await _safe_send(
            {
                "type": "error",
                "error_code": "E4013",
                "error_message": size_error,
            }
        )
        try:
            await websocket.close()
        except Exception:
            pass
        engine.decrement_connections()
        return

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
                _with_keepalive(
                    engine.transcribe(
                        bytes(all_audio),
                        language=language,
                        context=vocabulary.context_text(VOCABULARY),
                    ),
                    "stt",
                    _safe_send,
                ),
                timeout=600.0,
            )
            # 个人词库的替换规则:识别完就换,LLM 拿到的已经是改好的写法。
            result.text = vocabulary.apply_rules(result.text, VOCABULARY)

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
            if result.text.strip() and llm_active():
                await _safe_send(
                    {
                        "type": "llm_start",
                        "text": result.text[:50],
                    }
                )
                processed_text, llm_latency, llm_error = await _with_keepalive(
                    call_llm_server(
                        result.text, vocabulary_hint=vocabulary.llm_hint(VOCABULARY), lang=lang
                    ),
                    "llm",
                    _safe_send,
                )
            else:
                processed_text = result.text
                llm_latency = 0
                llm_error = None

            await _safe_send(
                {
                    "type": "result",
                    "text": processed_text,
                    "confidence": result.confidence,
                    "language": result.language,
                    "is_final": True,
                    "stt_latency_ms": result.stt_latency_ms,
                    "llm_latency_ms": llm_latency,
                    # 后处理开着却没做成时的原因(此时 text 是原文);没开或成功为 null。
                    # 老客户端不认这个字段,忽略即可,协议向后兼容。
                    "llm_error": llm_error,
                    "model": result.model,
                }
            )

        except TimeoutError:
            error_sent = True
            await _safe_send(
                {
                    "type": "error",
                    "error_code": "E5002",
                    # 客户端(gui/src-tauri/src/indicator.rs)按这句认出服务端超时。
                    "error_message": i18n.t(lang, "转写超时", "Transcription timed out"),
                }
            )
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            error_sent = True
            await _safe_send(
                {
                    "type": "error",
                    "error_code": "E5001",
                    "error_message": i18n.exc_text(lang, e),
                }
            )

    # 已发送 error 视为终态,不再发 done(避免客户端断开后 send 报错);
    # all_audio 为空时也照常发 done(与旧行为一致)
    if not (all_audio and error_sent):
        await _safe_send({"type": "done"})

    try:
        await websocket.close()
    except Exception as e:
        # 只吞「这个连接已经关掉了」这类正常异常。写成裸 `except:` 会连
        # asyncio.CancelledError 和 KeyboardInterrupt 一起吞掉 —— 它们继承的是
        # BaseException,不是 Exception。服务关停时任务取消正好投递在这一行,
        # 吞掉就等于把取消信号丢了。
        logger.debug(f"WebSocket close failed (already closed?): {e}")
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
        content = await _read_capped(file)
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

    except HTTPException:
        raise
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
