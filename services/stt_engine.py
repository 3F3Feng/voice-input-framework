"""
Voice Input Framework - STT 引擎模块

从 services/stt_server.py 拆出的引擎实现(L2:拆分大文件)。
包含 TranscriptionResult / TranscriptionRequest 数据模型与 STTEngine 引擎类。
"""

import asyncio
import logging

# 添加项目路径
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from shared.constants import AUDIO_SAMPLE_RATE
from shared.model_registry import IS_APPLE_SILICON, MODELS_CONFIG, get_default_model

logger = logging.getLogger("stt-server")


# ============== Data Models ==============
class TranscriptionResult(BaseModel):
    """转写结果"""

    text: str
    confidence: float = 1.0
    language: str = "auto"
    is_final: bool = True
    stt_latency_ms: float = 0.0
    model: str = ""


class TranscriptionRequest(BaseModel):
    """转写请求"""

    language: str = "auto"


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
    loaded_models: list[str]
    active_connections: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    diarize: dict[str, Any] | None = None


# ============== STT Engine ==============
class STTEngine:
    """STT 引擎管理器"""

    AVAILABLE_MODELS = MODELS_CONFIG

    def __init__(self, default_model: str = get_default_model()):
        self.default_model = default_model
        self.current_model_name = default_model
        self._model = None
        self._is_loaded = False
        self._loading = False
        self._load_lock = asyncio.Lock()
        self._model_info = self.AVAILABLE_MODELS.get(
            default_model, self.AVAILABLE_MODELS["qwen_asr_mlx_native_small"]
        )
        self.start_time = time.time()
        self.total_requests = 0
        self.failed_requests = 0
        self._active_connections = 0

    async def load(self) -> bool:
        """加载模型"""
        async with self._load_lock:
            if self._is_loaded:
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

        model_id = self._model_info["model_id"]
        engine_type = self._model_info.get("engine", "qwen_asr_mlx_native")

        # 检测设备
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

        # ── Whisper MLX 引擎 ──
        if engine_type == "whisper_mlx":
            if not IS_APPLE_SILICON:
                raise RuntimeError("MLX models require Apple Silicon (ARM64 + macOS)")
            import mlx_whisper
            import numpy as np

            logger.info(f"Loading MLX Whisper model: {model_id}...")
            # 触发预加载
            test_audio = np.zeros(AUDIO_SAMPLE_RATE, dtype=np.float32)
            mlx_whisper.transcribe(test_audio, path_or_hf_repo=model_id)
            self._model = {"model_id": model_id, "type": "whisper_mlx"}
            self._model_type = "whisper_mlx"
            return

        # ── Qwen3-ASR MLX 原生引擎 (mlx-audio) ──
        if engine_type == "qwen_asr_mlx_native":
            if not IS_APPLE_SILICON:
                raise RuntimeError("MLX models require Apple Silicon (ARM64 + macOS)")
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

        # ── Whisper.cpp 引擎 ──
        if engine_type == "whisper_cpp":
            from server.models.whisper_cpp import WhisperCppEngine

            whisper_model = self._model_info.get("whisper_model", "whisper-v3-base")
            logger.info(f"Loading Whisper.cpp model: {whisper_model}...")
            whisper_engine = WhisperCppEngine(model_name=whisper_model)

            # WhisperCppEngine 同步加载(内部无 await,直接调用即可)
            whisper_engine.load_sync()
            self._model = whisper_engine
            self._model_type = "whisper_cpp"
            return

        # ── Whisper Turbo (transformers) ──
        if engine_type == "whisper_turbo":
            from transformers import pipeline

            logger.info(f"Loading Whisper turbo on {device}...")
            self._model = pipeline(
                "automatic-speech-recognition",
                model=model_id,
                torch_dtype=torch.float16 if device == "cuda" else torch.float32,
                device=device,
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
        self._loading = False

        # 释放旧模型内存
        if self._model is not None:
            import gc

            import torch

            del self._model
            self._model = None
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
            gc.collect()
            logger.info("Old model memory released")

        # 在后台异步加载新模型
        async def load_in_background():
            try:
                success = await self.load()
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
        self,
        audio_data: bytes,
        language: str = "auto",
    ) -> TranscriptionResult:
        """转写音频"""
        import numpy as np

        start_time = time.time()
        self.total_requests += 1

        try:
            # 确保模型已加载
            if not self._is_loaded:
                success = await self.load()
                if not success:
                    raise RuntimeError("Failed to load STT model")

            # 转换音频
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            audio_array = audio_array.astype(np.float32) / 32768.0
            sample_rate = AUDIO_SAMPLE_RATE

            # 执行转写
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
            else:
                text, detected_lang = "", lang or language

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
                    result = await self._model.transcribe(
                        audio_data=audio_bytes,
                        language=lang or "auto",
                        sample_rate=sample_rate,
                    )
                    text = result.text
                    detected_lang = result.language

                # ── Whisper Turbo (transformers) ──
                elif getattr(self, "_model_type", None) == "whisper_turbo":
                    result = self._model(
                        audio_array,
                        generate_kwargs={"language": lang},
                    )
                    text = result.get("text", "").strip()
                    detected_lang = lang or "en"

                # ── Qwen3-ASR (transformers 或 MLX 环境) ──
                else:
                    results = self._model.transcribe(
                        audio=(audio_array, sample_rate),
                        language=lang,
                    )
                    if results and len(results) > 0:
                        text = results[0].text
                        detected_lang = results[0].language
            text = text.strip()

            latency = (time.time() - start_time) * 1000
            return TranscriptionResult(
                text=text,
                confidence=1.0,
                language=detected_lang or language,
                is_final=True,
                stt_latency_ms=latency,
                model=self.current_model_name,
            )
        except Exception as e:
            self.failed_requests += 1
            logger.error(f"Transcription error: {e}", exc_info=True)
            raise

    def is_loading(self) -> bool:
        return self._loading

    def is_model_loaded(self) -> bool:
        return self._is_loaded

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "active_connections": self._active_connections,
        }

    def increment_connections(self):
        self._active_connections += 1

    def decrement_connections(self):
        self._active_connections = max(0, self._active_connections - 1)
