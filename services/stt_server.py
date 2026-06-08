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
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from contextvars import ContextVar

import httpx

# 添加项目路径
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))
from shared.model_registry import MODELS_CONFIG, get_default_model
from shared.platform_detector import (
    detect_platform,
    get_startup_banner,
    check_resource_requirements,
)
from services.diarize_engine import DiarizationEngine, DIARIZE_ENABLED

import uvicorn
from contextlib import asynccontextmanager
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ============== Configuration ==============
STT_HOST = os.getenv("VIF_STT_HOST", "0.0.0.0")
STT_PORT = int(os.getenv("VIF_STT_PORT", "6544"))
STT_MODEL = os.getenv(
    "VIF_STT_MODEL",
    get_default_model(),
)
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
LLM_MODEL = os.getenv("VIF_LLM_MODEL", "Qwen3.5-4B-OptiQ")
_last_llm_model = LLM_MODEL  # Cached for WebSocket handler (no blocking)

# ============== LLM Fast-Fail Cache ==============
# Track LLM server availability to avoid slow connection attempts
_llm_available = True  # Start optimistic - first request will verify
_llm_last_check = 0.0  # Never checked yet
_llm_check_interval = 30.0  # Re-check every 30 seconds
_llm_timeout = 1.0  # Fast timeout for LLM requests (1 second)


def _is_llm_available() -> bool:
    """Check if LLM server is available (cached)"""
    global _llm_available, _llm_last_check
    now = time.time()

    # If we recently checked, return cached result
    if now - _llm_last_check < _llm_check_interval:
        return _llm_available

    # If we haven't checked yet, assume available (let first request verify)
    if _llm_last_check == 0.0:
        return True

    # Otherwise, return cached result
    return _llm_available


def _mark_llm_available(available: bool):
    """Update LLM availability cache"""
    global _llm_available, _llm_last_check
    _llm_available = available
    _llm_last_check = time.time()


# ============== State Persistence ==============
"""
持久化最后使用的 STT 模型和 LLM 开关状态，
避免服务器重启后需要重新设置。
"""
STATE_DIR = Path.home() / ".config" / "voice-input-framework"
STATE_FILE = STATE_DIR / "stt_state.json"

# ============== Early Logging (before full config) ==============
# Configure basic logging early so state persistence can log
_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=LOG_LEVEL, format=_log_format)
logger = logging.getLogger("stt-server")


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


# ============== Structured Logging (optional JSON mode) ==============
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


# Reconfigure with JSON format if requested
if os.getenv("VIF_LOG_JSON", "").lower() == "true":
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredLogFormatter())
    logger.handlers = [handler]


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
    is_available: bool = True
    memory_gb: float = 0.0


class HealthStatus(BaseModel):
    """健康状态"""

    status: str
    version: str = "2.0.0"
    uptime_seconds: float
    current_model: str
    loaded_models: List[str]
    active_connections: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    diarize: Optional[Dict[str, Any]] = None
    platform: Optional[Dict[str, Any]] = None


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
                        logger.warning(
                            f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay * (2**attempt))  # 指数退避
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
    # Fast-fail: skip if LLM server is known to be down
    if not _is_llm_available():
        return text, 0

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{LLM_SERVER_URL}/process",
                json={"text": text, "options": {}},
                headers={"X-Request-ID": request_id},
                timeout=_llm_timeout,
            )
            if response.status_code == 200:
                _mark_llm_available(True)
                data = response.json()
                return data.get("text", text), data.get("llm_latency_ms", 0)
            else:
                _mark_llm_available(False)
                logger.warning(f"LLM server returned {response.status_code}")
                return text, 0
    except Exception as e:
        _mark_llm_available(False)
        logger.debug(f"LLM server not available: {e}")
        return text, 0


# ============== STT Engine ==============
class STTEngine:
    """STT 引擎管理器"""

    AVAILABLE_MODELS = MODELS_CONFIG

    def __init__(self, default_model: str = get_default_model()):
        self.default_model = default_model
        self.current_model_name = default_model
        self._model = None
        self._aligner = None
        self._is_loaded = False
        self._aligner_loaded = False
        self._loading = False
        self._load_lock = asyncio.Lock()
        self._model_info = self.AVAILABLE_MODELS.get(
            default_model, self.AVAILABLE_MODELS["qwen_asr_mlx_native_small"]
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
                    engine_type = self._model_info.get("engine", "")
                    if engine_type == "qwen_asr_mlx_native":
                        # MLX 原生引擎：Metal stream 是 thread-local，必须在主线程加载
                        self._load_model_sync()
                    else:
                        await loop.run_in_executor(None, self._load_model_sync)
                    self._is_loaded = True
                    logger.info("STT model loaded successfully")

                # 加载 ForcedAligner（如果需要时间戳功能）
                if load_aligner and not self._aligner_loaded:
                    logger.info(f"Loading ForcedAligner: {self._model_info['aligner_id']}")
                    logger.warning(
                        "ForcedAligner not available (deprecated engine removed). Timestamps disabled."
                    )
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
        """同步加载主模型

        使用统一平台检测模块，自动选择最优设备和引擎。
        """
        import torch

        model_id = self._model_info["model_id"]
        engine_type = self._model_info.get("engine", "qwen_asr_mlx_native")

        # 使用统一平台检测
        platform_info = detect_platform()
        device = platform_info.best_backend

        # 对于非 MLX 引擎，使用 torch 设备
        if device == "mlx":
            # MLX 引擎不使用 torch 设备，单独处理
            torch_device = "cpu"  # 降级到 CPU 用于非 MLX 部分
        elif device == "cuda":
            torch_device = "cuda"
        elif device == "mps":
            torch_device = "mps"
        else:
            torch_device = "cpu"

        logger.info(
            f"Platform: {platform_info.system} {platform_info.arch}, Backend: {device}, GPU: {platform_info.gpu_info}"
        )

        # ── Whisper MLX 引擎 ──
        if engine_type == "whisper_mlx":
            if not platform_info.has_mlx:
                raise RuntimeError(
                    "MLX models require Apple Silicon (ARM64 + macOS) with mlx installed"
                )
            import mlx_whisper
            import numpy as np

            logger.info(f"Loading MLX Whisper model: {model_id}...")
            # 触发预加载
            test_audio = np.zeros(16000, dtype=np.float32)
            mlx_whisper.transcribe(test_audio, path_or_hf_repo=model_id)
            self._model = {"model_id": model_id, "type": "whisper_mlx"}
            self._model_type = "whisper_mlx"
            return

        # ── Qwen3-ASR MLX 原生引擎 (mlx-audio) ──
        if engine_type == "qwen_asr_mlx_native":
            if not platform_info.has_mlx:
                raise RuntimeError(
                    "MLX models require Apple Silicon (ARM64 + macOS) with mlx installed"
                )
            from server.models.qwen3_asr_mlx_native import Qwen3ASRMLXNativeEngine

            model_name = self.current_model_name
            logger.info(f"Loading Qwen3-ASR MLX native model: {model_name}")
            native_engine = Qwen3ASRMLXNativeEngine(model_name=model_name)
            # 同步加载（MLX Metal 必须在本线程执行）
            native_engine._load_sync()
            native_engine._is_loaded = True
            self._model = native_engine
            self._model_type = "qwen_asr_mlx_native"
            return

        # ── Qwen3-ASR CUDA 引擎 ──
        if engine_type == "qwen_asr_cuda":
            if not platform_info.has_cuda:
                raise RuntimeError("CUDA models require NVIDIA GPU with CUDA support")
            from server.models.qwen3_asr_cuda import Qwen3ASRCudaEngine

            model_name = self.current_model_name
            logger.info(f"Loading Qwen3-ASR CUDA model: {model_name}")
            cuda_engine = Qwen3ASRCudaEngine(model_name=model_name)
            # CUDA 引擎同步加载
            cuda_engine._load_sync()
            cuda_engine._is_loaded = True
            self._model = cuda_engine
            self._model_type = "qwen_asr_cuda"
            return

        # ── Whisper.cpp 引擎 ──
        if engine_type == "whisper_cpp":
            if not platform_info.is_macos:
                raise RuntimeError("Whisper.cpp requires macOS")
            from server.models.whisper_cpp import WhisperCppEngine

            whisper_model = self._model_info.get("whisper_model", "whisper-v3-base")
            logger.info(f"Loading Whisper.cpp model: {whisper_model}...")
            whisper_engine = WhisperCppEngine(model_name=whisper_model)

            # WhisperCppEngine 同步加载
            import asyncio

            asyncio.run(whisper_engine.load())
            self._model = whisper_engine
            self._model_type = "whisper_cpp"
            return

        # ── Whisper Turbo (transformers) ──
        if engine_type == "whisper_turbo":
            from transformers import pipeline

            logger.info(f"Loading Whisper turbo on {torch_device}...")
            self._model = pipeline(
                "automatic-speech-recognition",
                model=model_id,
                torch_dtype=torch.float16 if torch_device == "cuda" else torch.float32,
                device=torch_device,
            )
            self._model_type = "whisper_turbo"
            return

        # ── 未匹配引擎 ──
        raise ValueError(f"Unknown engine type: {engine_type} for model: {model_id}")

    async def switch_model(self, model_name: str) -> dict:
        """
        切换到指定的 STT 模型

        Args:
            model_name: 模型名称 (如 "qwen_asr_mlx_native_small", "whisper_turbo")

        Returns:
            dict: 包含切换状态的字典
        """
        if model_name not in self.AVAILABLE_MODELS:
            raise ValueError(
                f"Unknown model: {model_name}. Available: {list(self.AVAILABLE_MODELS.keys())}"
            )

        # 如果已经是当前模型且已加载，直接返回
        if model_name == self.current_model_name and self._is_loaded:
            logger.info(f"Model {model_name} is already loaded")
            return {
                "status": "success",
                "message": f"Model {model_name} is already loaded",
                "current_model": self.current_model_name,
                "is_loaded": True,
                "is_loading": False,
            }

        logger.info(f"Switching from {self.current_model_name} to {model_name}")

        # 更新模型信息
        self.current_model_name = model_name
        self._model_info = self.AVAILABLE_MODELS[model_name]

        # 重置状态
        self._is_loaded = False
        self._aligner_loaded = False
        self._loading = False

        # 释放旧模型内存
        if self._model is not None:
            import gc
            import torch

            del self._model
            self._model = None

            # 使用统一平台检测清理 GPU 内存
            platform_info = detect_platform()
            if platform_info.has_mps:
                torch.mps.empty_cache()
            elif platform_info.has_cuda:
                torch.cuda.empty_cache()
            gc.collect()
            logger.info("Old model memory released")

        # 在后台异步加载新模型
        async def load_in_background():
            try:
                success = await self.load(load_aligner=False)
                if success:
                    logger.info(f"Model {model_name} loaded successfully")
                else:
                    logger.error(f"Failed to load model {model_name}")
            except Exception as e:
                logger.error(f"Error loading model {model_name}: {e}")

        # 启动后台加载任务
        asyncio.create_task(load_in_background())

        return {
            "status": "success",
            "message": f"Switching to {model_name}",
            "current_model": self.current_model_name,
            "is_loaded": False,
            "is_loading": True,
            "note": "Model is loading in background",
        }

    async def transcribe(
        self, audio_data: bytes, language: str = "auto", return_timestamps: bool = False
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
            if (
                return_timestamps
                and not self._aligner_loaded
                and getattr(self, "_model_type", None) != "whisper_cpp"
            ):
                success = await self.load(load_aligner=True)
                if not success:
                    logger.warning("Failed to load ForcedAligner, returning without timestamps")
                    return_timestamps = False

            # 转换音频
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            audio_array = audio_array.astype(np.float32) / 32768.0
            sample_rate = 16000

            # 执行转写
            asyncio.get_event_loop()
            lang = None if language == "auto" else language

            # ── MLX 原生引擎 (mlx-audio) ── 必须在加载模型的同一线程执行
            model_type = getattr(self, "_model_type", None)
            if model_type == "qwen_asr_mlx_native":
                result = await self._model.transcribe(
                    audio=(audio_array, sample_rate),
                    language=lang or "auto",
                    sample_rate=sample_rate,
                )
                text, detected_lang = result.text, result.language

            # ── Qwen3-ASR CUDA 引擎 ──
            elif model_type == "qwen_asr_cuda":
                result = await self._model.transcribe(
                    audio=(audio_array, sample_rate),
                    language=lang or "auto",
                    sample_rate=sample_rate,
                )
                text, detected_lang = result.text, result.language

            else:
                # ── Whisper MLX 引擎 ──
                if getattr(self, "_model_type", None) == "whisper_mlx":
                    import mlx_whisper

                    model_id = self._model["model_id"]
                    result = mlx_whisper.transcribe(
                        audio_array,
                        path_or_hf_repo=model_id,
                        language=lang,
                        return_timestamps=True,
                    )
                    text = result.get("text", "").strip()
                    detected_lang = result.get("language", lang or "en")

                # ── Whisper.cpp 引擎 ──
                elif getattr(self, "_model_type", None) == "whisper_cpp":
                    import numpy as np

                    # whisper.cpp 需要 bytes
                    audio_bytes = (audio_array * 32768).astype(np.int16).tobytes()
                    result = asyncio.run(
                        self._model.transcribe(
                            audio_data=audio_bytes,
                            language=lang or "auto",
                            sample_rate=sample_rate,
                        )
                    )
                    text = result.text
                    detected_lang = result.language

                # ── Whisper Turbo (transformers) ──
                elif getattr(self, "_model_type", None) == "whisper_turbo":
                    result = self._model(
                        audio_array,
                        generate_kwargs={"language": lang},
                    )
                    # Pipeline returns list of dicts
                    if isinstance(result, list) and len(result) > 0:
                        text = result[0].get("text", "").strip()
                    elif isinstance(result, dict):
                        text = result.get("text", "").strip()
                    else:
                        text = str(result).strip()
                    detected_lang = lang or "en"

                # ── Qwen3-ASR (transformers 或 MLX 环境) ──
                else:
                    results = await self._model.transcribe(
                        audio=(audio_array, sample_rate),
                        language=lang,
                    )
                    if results and len(results) > 0:
                        text = results[0].text
                        detected_lang = results[0].language
                    else:
                        text = ""
                        detected_lang = language

            text = text.strip()

            # 生成时间戳（如果需要）
            timestamps = None
            if (
                return_timestamps
                and text
                and self._aligner_loaded
                and getattr(self, "_model_type", None) != "whisper_cpp"
            ):
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
        self, audio_array, sample_rate: int, text: str, language: str
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
            except Exception:
                pass

            # 转换结果格式
            if results and hasattr(results, "segments"):
                timestamps = []
                for segment in results.segments:
                    for word_info in segment.get("words", []):
                        timestamps.append(
                            WordTimestamp(
                                word=word_info.get("word", ""),
                                start=word_info.get("start", 0.0),
                                end=word_info.get("end", 0.0),
                            )
                        )
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


# ============== Diarization Engine ==============
diarize_engine = DiarizationEngine() if DIARIZE_ENABLED else None

# ============== FastAPI App ==============
engine = STTEngine(default_model=STT_MODEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup
    extra_info = {
        "Default STT Model": STT_MODEL,
        "LLM Enabled": LLM_ENABLED,
        "LLM Model": LLM_MODEL,
    }
    banner = get_startup_banner("STT Service", extra_info)
    logger.info(banner)

    # 资源检查
    from shared.model_registry import MODELS_CONFIG

    model_config = MODELS_CONFIG.get(STT_MODEL, {})
    required_memory = model_config.get("memory_gb", 0)
    resource_result = check_resource_requirements(STT_MODEL, required_memory)

    if resource_result["passed"]:
        logger.info("✓ Resource check passed")
    else:
        for warning in resource_result["warnings"]:
            logger.warning(f"⚠️  {warning}")

    # 预加载配置
    preload = os.getenv("VIF_PRELOAD_MODELS", "stt").lower()
    if preload == "none":
        logger.info("Preload disabled (VIF_PRELOAD_MODELS=none)")
    elif preload == "stt":
        logger.info("Preloading STT model...")
        asyncio.create_task(engine.load())
    elif preload == "all":
        logger.info("Preloading STT model...")
        asyncio.create_task(engine.load())
    else:
        logger.info(f"Unknown preload option: {preload}, defaulting to STT")
        asyncio.create_task(engine.load())

    # Start LLM health check background task
    async def llm_health_checker():
        """Periodically check LLM server availability"""
        # Probe immediately on startup
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=2.0)
                if resp.status_code == 200:
                    _mark_llm_available(True)
                    logger.info("LLM server detected on startup")
                else:
                    _mark_llm_available(False)
                    logger.info("LLM server not responding")
        except Exception:
            _mark_llm_available(False)
            logger.info("LLM server not available (will retry every 30s)")

        # Then check periodically
        while True:
            await asyncio.sleep(_llm_check_interval)
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=2.0)
                    if resp.status_code == 200:
                        if not _is_llm_available():
                            logger.info("LLM server is back online")
                        _mark_llm_available(True)
                    else:
                        _mark_llm_available(False)
            except Exception:
                _mark_llm_available(False)

    if LLM_ENABLED:
        asyncio.create_task(llm_health_checker())
        logger.info("LLM health checker started")

    logger.info(f"Starting STT Service on {STT_HOST}:{STT_PORT}")

    yield

    # Shutdown
    logger.info("STT Service shutting down")


app = FastAPI(
    title="Voice Input Framework - STT Service",
    description="独立的语音识别服务，使用 Qwen3-ASR",
    version="2.0.0",
    lifespan=lifespan,
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
            extra={"request_id": request_id, "duration_ms": duration},
        )
        return response
    except Exception as e:
        logger.error(f"Request failed: {e}", extra={"request_id": request_id})
        raise


@app.get("/platform")
async def get_platform():
    """获取平台信息"""
    platform_info = detect_platform()
    from shared.model_selector import ModelSelector

    selector = ModelSelector(platform_info)

    return {
        "system": platform_info.system,
        "arch": platform_info.arch,
        "python_version": platform_info.python_version,
        "backend": platform_info.best_backend,
        "gpu": {
            "name": platform_info.cuda_device.name if platform_info.cuda_device else None,
            "memory_gb": platform_info.cuda_device.memory_gb if platform_info.cuda_device else 0,
            "driver": platform_info.cuda_device.driver_version
            if platform_info.cuda_device
            else None,
            "cuda_version": platform_info.cuda_device.cuda_version
            if platform_info.cuda_device
            else None,
        }
        if platform_info.has_cuda
        else None,
        "cpu": {
            "cores": platform_info.cpu_cores,
            "ram_gb": round(platform_info.ram_gb, 1),
        },
        "capabilities": {
            "mlx": platform_info.has_mlx,
            "cuda": platform_info.has_cuda,
            "mps": platform_info.has_mps,
        },
        "recommended": {
            "stt_model": selector.get_default_model(),
            "llm_models": platform_info.get_recommended_llm_models(),
        },
        "available_models": list(selector.get_available_models().keys()),
    }


@app.get("/health", response_model=HealthStatus)
async def health_check():
    """健康检查"""
    platform_info = detect_platform()
    return HealthStatus(
        status="ok" if engine.is_model_loaded() else "loading",
        version="2.0.0",
        uptime_seconds=time.time() - engine.start_time,
        current_model=engine.current_model_name,
        loaded_models=[engine.current_model_name] if engine.is_model_loaded() else [],
        active_connections=engine._active_connections,
        total_requests=engine.total_requests,
        failed_requests=engine.failed_requests,
        diarize=diarize_engine.get_health() if diarize_engine else {"status": "disabled"},
        platform={
            "system": platform_info.system,
            "arch": platform_info.arch,
            "backend": platform_info.best_backend,
            "gpu": platform_info.gpu_info,
        },
    )


@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    """获取可用 STT 模型列表"""
    platform_info = detect_platform()
    from shared.model_selector import ModelSelector

    selector = ModelSelector(platform_info)

    models = []
    for name, info in STTEngine.AVAILABLE_MODELS.items():
        # 检查模型是否在当前平台可用
        available_models = selector.get_available_models()
        is_available = name in available_models

        models.append(
            ModelInfo(
                name=name,
                description=info.get("description", f"STT model: {info['model_id']}"),
                is_loaded=(name == engine.current_model_name and engine.is_model_loaded()),
                is_default=(name == engine.default_model),
                is_available=is_available,
                memory_gb=info.get("memory_gb", 0),
            )
        )
    return models


# ============== LLM 转发 API ==============


@app.get("/llm/models")
async def list_llm_models():
    """转发：获取可用 LLM 模型列表"""
    # Fast-fail: skip if LLM server is known to be down
    if not _is_llm_available():
        return {"models": [], "error": "LLM server not available", "cached": True}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{LLM_SERVER_URL}/models", timeout=_llm_timeout)
            if resp.status_code == 200:
                _mark_llm_available(True)
                data = resp.json()
                # 包装成客户端期望的格式
                if isinstance(data, list):
                    return {"models": data}
                return data
            else:
                _mark_llm_available(False)
                return {"error": f"LLM server returned {resp.status_code}"}
    except Exception as e:
        _mark_llm_available(False)
        logger.debug(f"LLM server not available: {e}")
        return {"models": [], "error": str(e)}


@app.post("/llm/models/select")
async def select_llm_model(request: Request):
    """转发：选择 LLM 模型"""
    if not _is_llm_available():
        return {"error": "LLM server not available"}

    try:
        body = await request.json()
        model_name = body.get("model_name", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{LLM_SERVER_URL}/models/select", data={"model_name": model_name}, timeout=30.0
            )
            if resp.status_code == 200:
                _mark_llm_available(True)
                # 持久化 LLM 模型选择
                state = load_state()
                state["llm_model"] = model_name
                save_state(state)
                logger.info(f"LLM model saved to state: {model_name}")
                return resp.json()
            else:
                return {"error": f"LLM server returned {resp.status_code}"}
    except Exception as e:
        _mark_llm_available(False)
        logger.error(f"Failed to select LLM model: {e}")
        return {"error": str(e)}


@app.get("/llm/health")
async def llm_health():
    """转发：LLM 服务器健康检查"""
    if not _is_llm_available():
        return {"status": "offline", "error": "LLM server not available"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{LLM_SERVER_URL}/health", timeout=_llm_timeout)
            _mark_llm_available(True)
            return resp.json()
    except Exception as e:
        _mark_llm_available(False)
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
    if not _is_llm_available():
        return {"prompt": "", "error": "LLM server not available"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{LLM_SERVER_URL}/prompt", timeout=_llm_timeout)
            if resp.status_code == 200:
                _mark_llm_available(True)
                return resp.json()
            return {"error": f"LLM server returned {resp.status_code}"}
    except Exception as e:
        _mark_llm_available(False)
        return {"error": str(e)}


@app.put("/llm/prompt")
async def update_llm_prompt(request: Request):
    """转发：更新 LLM 提示词"""
    if not _is_llm_available():
        return {"error": "LLM server not available"}

    try:
        body = await request.json()
        async with httpx.AsyncClient() as client:
            resp = await client.put(f"{LLM_SERVER_URL}/prompt", json=body, timeout=_llm_timeout)
            if resp.status_code == 200:
                _mark_llm_available(True)
                return resp.json()
            return {"error": f"LLM server returned {resp.status_code}"}
    except Exception as e:
        _mark_llm_available(False)
        return {"error": str(e)}


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
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")


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
    return_timestamps: bool = Form(False),
):
    """转写音频文件"""
    req_id = request_id_ctx.get()
    try:
        audio_content = await file.read()
        result = await engine.transcribe(
            audio_content, language=language, return_timestamps=return_timestamps
        )
        return result
    except Exception as e:
        logger.error(f"Transcription error: {e}", extra={"request_id": req_id})
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket 流式识别"""
    ws_start = time.time()
    await websocket.accept()
    engine.increment_connections()
    logger.info(f"[timing] WebSocket accepted (+{(time.time() - ws_start) * 1000:.0f}ms)")

    # Use cached LLM status (don't block connection)
    llm_info = {"llm_enabled": LLM_ENABLED, "llm_model": _last_llm_model}

    # 发送就绪消息 immediately
    await websocket.send_text(
        json.dumps(
            {
                "type": "ready",
                "model": engine.current_model_name,
                "is_loading": engine.is_loading(),
                "aligner_loaded": engine.is_aligner_loaded(),
                "llm_enabled": llm_info["llm_enabled"],
                "llm_model": llm_info["llm_model"],
            }
        )
    )
    logger.info(f"[timing] Ready sent (+{(time.time() - ws_start) * 1000:.0f}ms)")

    audio_queue = asyncio.Queue()
    asyncio.Event()
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
        except asyncio.TimeoutError:
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
                    return_timestamps = data.get("return_timestamps", False)
                    language = data.get("language", "auto")
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "config_ack",
                                "return_timestamps": return_timestamps,
                                "language": language,
                            }
                        )
                    )

                elif msg_type in ("end", "stop"):
                    await audio_queue.put(None)  # 通知 stream 结束
                    break
        except asyncio.TimeoutError:
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
    audio_recv_time = time.time()
    logger.info(f"[timing] Audio received (+{(audio_recv_time - ws_start) * 1000:.0f}ms)")

    # 收集所有音频数据
    all_audio = bytearray()
    while not audio_queue.empty():
        chunk = audio_queue.get_nowait()
        if chunk is not None:
            all_audio.extend(chunk)

    if all_audio:
        try:
            audio_size_kb = len(all_audio) / 1024
            logger.info(f"[timing] Audio: {audio_size_kb:.1f}KB, starting transcription...")

            result = await asyncio.wait_for(
                engine.transcribe(
                    bytes(all_audio),
                    language=language,
                ),
                timeout=600.0,
            )
            stt_done_time = time.time()
            logger.info(
                f"[timing] STT done (+{(stt_done_time - ws_start) * 1000:.0f}ms, stt={result.stt_latency_ms:.0f}ms)"
            )

            # 发送 STT 结果
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "stt_result",
                        "text": result.text,
                        "stt_latency_ms": result.stt_latency_ms,
                        "confidence": result.confidence,
                        "language": result.language,
                        "model": result.model,
                    }
                )
            )

            # LLM 后处理
            if result.text.strip() and LLM_ENABLED:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "llm_start",
                            "text": result.text[:50],
                        }
                    )
                )
                processed_text, llm_latency = await call_llm_server(result.text)
                llm_done_time = time.time()
                logger.info(
                    f"[timing] LLM done (+{(llm_done_time - ws_start) * 1000:.0f}ms, llm={llm_latency:.0f}ms)"
                )
            else:
                processed_text = result.text
                llm_latency = 0

            total_ms = (time.time() - ws_start) * 1000
            logger.info(
                f"[timing] TOTAL: {total_ms:.0f}ms (recv={((audio_recv_time - ws_start) * 1000):.0f}ms, stt={result.stt_latency_ms:.0f}ms, llm={llm_latency:.0f}ms)"
            )

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "result",
                        "text": processed_text,
                        "confidence": result.confidence,
                        "language": result.language,
                        "is_final": True,
                        "stt_latency_ms": result.stt_latency_ms,
                        "llm_latency_ms": llm_latency,
                        "total_latency_ms": total_ms,
                        "model": result.model,
                    }
                )
            )

        except asyncio.TimeoutError:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "error_code": "E5002",
                        "error_message": "转写超时",
                    }
                )
            )
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "error_code": "E5001",
                        "error_message": str(e),
                    }
                )
            )

    await websocket.send_text(json.dumps({"type": "done"}))

    try:
        await websocket.close()
    except Exception:
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
    num_speakers: Optional[int] = Form(None),
    min_speakers: Optional[int] = Form(None),
    max_speakers: Optional[int] = Form(None),
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
    logger.info(f"Starting STT Service on {STT_HOST}:{STT_PORT}")
    uvicorn.run(
        app,
        host=STT_HOST,
        port=STT_PORT,
        log_level=LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
