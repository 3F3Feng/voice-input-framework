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
from collections.abc import Callable
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
    #: 实际选中的推理后端与机器画像(设备 / 精度 / 核数 / 内存 / 显存)。
    #: 客户端和排查问题的人靠它判断这台机器到底跑在 GPU 上还是 CPU 上。
    hardware: dict[str, Any] | None = None
    #: 最近一次加载失败的原因(status == "error" 时有值)。没有它,加载失败和
    #: 「还在加载」在外面看起来一模一样,界面会永远停在「正在加载模型」。
    error: str | None = None


# ============== STT Engine ==============
class STTEngine:
    """STT 引擎管理器"""

    AVAILABLE_MODELS = MODELS_CONFIG

    def __init__(self, default_model: str = get_default_model()):
        self.default_model = default_model
        self.current_model_name = default_model
        self._model = None
        self._model_type = None
        self._is_loaded = False
        self._loading = False
        self._load_error: str | None = None
        #: 切换失败的模型 → 失败原因。切换失败会回退到上一个模型,`_load_error`
        #: 随之清空,原因就只能记在这里,好让 `/models/status/{name}` 答得出来。
        self._switch_errors: dict[str, str] = {}
        self._load_lock = asyncio.Lock()
        # 实际选中的推理后端,加载模型时填上。/health 会如实报出来 —— 用户
        # (和我们)得能一眼看出这台机器到底跑在 GPU 上还是 CPU 上。
        self._backend = None
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
            self._load_error = None
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
                self._load_error = f"{type(e).__name__}: {e}"
                self.failed_requests += 1
                return False
            finally:
                self._loading = False

    def _load_model_sync(self):
        """同步加载主模型"""
        model_id = self._model_info["model_id"]
        engine_type = self._model_info.get("engine", "qwen_asr_mlx_native")

        # 后端选择集中在 services/device.py:那里认得出 ROCm(否则 A 卡会被
        # 报成 N 卡)和 Intel XPU,也会按硬件挑精度,而不是「只有 CUDA 用 fp16」。
        from services.device import detect as detect_backend

        backend = detect_backend()
        device = backend.torch_device
        self._backend = backend

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

            logger.info(f"Loading Whisper on {backend.detail} [{backend.dtype_name}]...")
            self._model = pipeline(
                "automatic-speech-recognition",
                model=model_id,
                torch_dtype=backend.torch_dtype(),
                device=device,
            )
            self._model_type = "whisper_turbo"
            return

        # ── 未匹配引擎 ──
        raise ValueError(f"Unknown engine type: {engine_type} for model: {model_id}")

    async def switch_model(
        self,
        model_name: str,
        on_loaded: Callable[[str], None] | None = None,
    ) -> dict:
        """
        切换到指定的 STT 模型(立即返回,后台加载)

        Args:
            model_name: 模型名称 (如 "qwen_asr_mlx_native_small", "whisper_turbo")
            on_loaded: 新模型**真正加载成功之后**才调用,用来持久化选择。以前选择在
                加载之前就写进状态文件,切到一个坏模型之后,重启服务还会接着加载它。

        新模型加载失败时回退到切换前的模型(如果它当时是可用的),失败原因记在
        `switch_error(model_name)` 里。旧模型必须先释放再加载新的 —— 两个都留在
        内存里,小内存机器上会直接 OOM —— 所以回退意味着把旧模型重新加载一遍。

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
            if on_loaded:
                on_loaded(model_name)
            return {
                "status": "success",
                "message": f"Model {model_name} is already loaded",
                "current_model": self.current_model_name,
                "is_loaded": True,
                "is_loading": False,
            }

        logger.info(f"Switching from {self.current_model_name} to {model_name}")
        # 只有切换前那个模型是真能用的,才值得回退过去。
        previous = self.current_model_name if self._is_loaded else None

        # 切换与加载必须互斥:否则卸载旧模型时可能有 load() 正在写 _model/_model_type,
        # 导致状态错乱。(并发的 transcribe() 已在内部取本地引用,不会用到半释放的实例。)
        async with self._load_lock:
            self._reset_to(model_name)
        self._switch_errors.pop(model_name, None)

        # 在后台异步加载新模型
        async def load_in_background():
            try:
                success = await self.load()
            except Exception as e:  # noqa: BLE001 - load() 自己已经兜过,这里只是保险
                self._load_error = f"{type(e).__name__}: {e}"
                success = False
            if success:
                logger.info(f"Model {model_name} loaded successfully")
                if on_loaded:
                    on_loaded(model_name)
                return
            reason = self._load_error or "未知原因"
            self._switch_errors[model_name] = reason
            logger.error(f"Failed to load model {model_name}: {reason}")
            # 已经被别的切换取代了(用户又选了另一个),就别再回退。
            if previous is None or self.current_model_name != model_name:
                return
            logger.info(f"Rolling back to previous model {previous}")
            async with self._load_lock:
                self._reset_to(previous)
            if not await self.load():
                logger.error(f"Rollback to {previous} failed as well: {self._load_error}")

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

    def _reset_to(self, model_name: str) -> None:
        """把当前模型指向 `model_name` 并释放旧模型。调用方须持有 `_load_lock`。"""
        self.current_model_name = model_name
        self._model_info = self.AVAILABLE_MODELS[model_name]
        self._is_loaded = False
        self._loading = False
        self._load_error = None

        # 释放旧模型内存
        if self._model is not None:
            import gc

            self._model = None
            self._model_type = None
            try:
                import torch

                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
            except ImportError:
                # MLX 模型不需要 torch;没装就不必清它的缓存。
                pass
            gc.collect()
            logger.info("Old model memory released")

    def switch_error(self, model_name: str) -> str | None:
        """这个模型最近一次切换失败的原因;没失败过或正在重试时为 None。"""
        if model_name == self.current_model_name and self._loading:
            return None
        return self._switch_errors.get(model_name)

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
                    # 带上真正的原因。以前只剩一句 "Failed to load STT model",
                    # 用户在界面上看到它,完全不知道该去修什么。
                    raise RuntimeError(
                        f"STT 模型 {self.current_model_name} 加载失败:"
                        f"{self._load_error or '原因未知,见服务日志'}"
                    )

            # 转换音频
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            audio_array = audio_array.astype(np.float32) / 32768.0
            sample_rate = AUDIO_SAMPLE_RATE

            # 执行转写
            lang = None if language == "auto" else language

            # 取本地引用:并发的 switch_model() 会把 _model/_model_type 置空,
            # 本地引用保证本次转写用同一个(且完整的)实例跑完。
            model = self._model
            model_type = getattr(self, "_model_type", None)
            if model is None:
                raise RuntimeError("STT model is not available (switching?)")

            # ── MLX 原生引擎 (mlx-audio) ── 必须在加载模型的同一线程执行
            if model_type == "qwen_asr_mlx_native":
                result = await model.transcribe(
                    audio=(audio_array, sample_rate),
                    language=lang or "auto",
                    sample_rate=sample_rate,
                )
                text, detected_lang = result.text, result.language
            else:
                text, detected_lang = "", lang or language

                # ── Whisper MLX 引擎 ──
                if model_type == "whisper_mlx":
                    import mlx_whisper

                    model_id = model["model_id"]
                    result = mlx_whisper.transcribe(
                        audio_array,
                        path_or_hf_repo=model_id,
                        language=lang,
                        return_timestamps=True,
                    )
                    text = result.get("text", "").strip()
                    detected_lang = result.get("language", lang or "en")

                # ── Whisper.cpp 引擎 ──
                elif model_type == "whisper_cpp":
                    import numpy as np

                    # whisper.cpp 需要 bytes
                    audio_bytes = (audio_array * 32768).astype(np.int16).tobytes()
                    result = await model.transcribe(
                        audio_data=audio_bytes,
                        language=lang or "auto",
                        sample_rate=sample_rate,
                    )
                    text = result.text
                    detected_lang = result.language

                # ── Whisper Turbo (transformers) ──
                elif model_type == "whisper_turbo":
                    # return_timestamps=True 不能省:音频超过 30 秒(3000 帧 mel)时
                    # transformers 会自动走长音频逐段生成,而那条路要求模型预测时间戳,
                    # 不开就直接抛 ValueError。以前 Windows / Linux 上(默认就是这个
                    # 引擎)说话超过 30 秒必然转写失败。实测 transformers 5.17 +
                    # whisper-tiny:35 秒音频不带它报错,带上正常出结果。
                    result = model(
                        audio_array,
                        return_timestamps=True,
                        generate_kwargs={"language": lang},
                    )
                    text = result.get("text", "").strip()
                    detected_lang = lang or "en"

                # ── Qwen3-ASR (transformers 或 MLX 环境) ──
                else:
                    results = model.transcribe(
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

    def backend_info(self) -> dict | None:
        """选中的推理后端(设备 / 精度 / 说明)。还没加载模型时为 None。"""
        return self._backend.as_dict() if self._backend else None

    def is_model_loaded(self) -> bool:
        return self._is_loaded

    def load_error(self) -> str | None:
        """最近一次加载失败的原因;正在加载或已加载成功时为 None。"""
        return None if (self._is_loaded or self._loading) else self._load_error

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
