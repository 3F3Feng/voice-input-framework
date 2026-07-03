#!/usr/bin/env python3
"""
Voice Input Framework - CUDA Qwen3-ASR 引擎 (优化版)

性能优化策略:
1. bfloat16 代替 float16 - 更好的数值稳定性
2. Flash Attention 2 - 减少 20-30% 推理时间
3. 模型同步预热 - 避免首次推理延迟 (即使通过 stt_server._load_model_sync 加载)
4. Greedy decoding - 最快解码策略
5. VAD 静音检测 - 白录音/空音频直接返回不推理
"""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator

import numpy as np

# Suppress the harmless 'temperature' warning from qwen_asr package
# (qwen_asr internally passes temperature to transformers.generate() which ignores it)
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)

# Minimum audio RMS threshold to consider as non-silence (VAD)
_VAD_RMS_THRESHOLD = 0.01


class Qwen3ASRCudaEngine(BaseSTTEngine):
    """CUDA 版 Qwen3-ASR 引擎 (优化版)"""

    MODEL_CONFIGS = {
        "qwen_asr": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "memory_gb": 3.5,
            "description": "Qwen3-ASR-1.7B CUDA (推荐)",
            "use_bf16": True,
        },
        "qwen_asr_small": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "memory_gb": 1.5,
            "description": "Qwen3-ASR-0.6B CUDA (更快)",
            "use_bf16": True,
        },
        "qwen_asr_int8": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "memory_gb": 2.0,
            "description": "Qwen3-ASR-1.7B CUDA int8 (省内存)",
            "use_bf16": False,
            "quantize": "int8",
        },
        "qwen_asr_small_int8": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "memory_gb": 1.0,
            "description": "Qwen3-ASR-0.6B CUDA int8 (最快)",
            "use_bf16": False,
            "quantize": "int8",
        },
    }

    def __init__(self, model_name: str = "qwen_asr", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model = None
        self.model_config = self.MODEL_CONFIGS.get(model_name, self.MODEL_CONFIGS["qwen_asr"])
        self._device = None
        self._warmed_up = False

    async def load(self) -> None:
        if self._is_loaded:
            return
        logger.info(f"Loading CUDA model: {self.model_config['model_id']}")
        try:
            self._load_sync()
            self._is_loaded = True
            logger.info(f"Model loaded on {self._device}: {self.model_config['model_id']}")
        except Exception as e:
            raise STTEngineError(f"Failed to load CUDA model: {e}")

    def _load_sync(self):
        """同步加载模型"""
        import torch

        model_id = self.model_config["model_id"]
        use_bf16 = self.model_config.get("use_bf16", True)
        quantize = self.model_config.get("quantize")

        if not torch.cuda.is_available():
            raise STTEngineError("CUDA is not available on this machine")

        self._device = "cuda:0"

        # 清理 GPU 缓存
        torch.cuda.empty_cache()

        # 使用 qwen_asr 官方包
        from qwen_asr import Qwen3ASRModel

        # 设置 dtype - bfloat16 is recommended for inference
        if quantize == "int8":
            dtype = torch.int8
            logger.info("Using int8 quantization")
        elif use_bf16 and torch.cuda.is_bf16_supported():
            dtype = torch.bfloat16
            logger.info("Using bfloat16 (optimal for inference)")
        else:
            dtype = torch.float16
            logger.info("Using float16")

        # 尝试 Flash Attention 2
        attn_impl = "sdpa"  # Default - PyTorch Scaled Dot Product Attention
        try:
            import flash_attn  # noqa: F401

            attn_impl = "flash_attention_2"
            logger.info("Flash Attention 2 available, using it")
        except ImportError:
            logger.info("Flash Attention 2 not available (using SDPA)")
            logger.info("  SDPA with bfloat16 is fast enough for voice input")
            # Don't recommend flash-attn on Windows - CUDA version mismatch issues

        # 加载模型
        logger.info(f"Loading model with attn_implementation={attn_impl}")
        self._model = Qwen3ASRModel.from_pretrained(
            model_id,
            dtype=dtype,
            device_map=self._device,
            attn_implementation=attn_impl,
            max_inference_batch_size=1,  # Single user, minimize memory
            max_new_tokens=256,
        )

        logger.info(f"Model loaded on {self._device}")

        # 同步预热：初始化 CUDA kernels
        # 注意：预热放在 _load_sync() 而不是 async _warmup() 中，
        # 因为 stt_server._load_model_sync() 直接调用 _load_sync()，
        # 跳过了 async load() 的预热步骤（这是之前的 bug）
        if not self._warmed_up:
            logger.info("Warming up model (synchronously)...")
            warmup_start = time.time()
            try:
                warmup_audio = np.zeros(16000, dtype=np.float32)  # 1 second silence
                self._model.transcribe(
                    audio=(warmup_audio, 16000),
                    language=None,
                )
                elapsed = time.time() - warmup_start
                logger.info(f"Warmup complete in {elapsed:.2f}s")
                self._warmed_up = True
            except Exception as e:
                logger.warning(f"Warmup failed (non-fatal): {e}")

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._model = None
        self._device = None
        self._warmed_up = False
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        self._is_loaded = False
        logger.info("CUDA model unloaded, VRAM released")

    def _convert_audio(self, audio_data: bytes, sample_rate: int = 16000) -> np.ndarray:
        """将音频 bytes 转换为 float32 一维数组 (16kHz mono)"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_array = audio_array.astype(np.float32) / 32768.0

        if sample_rate != 16000:
            target_length = int(len(audio_array) * 16000 / sample_rate)
            audio_array = np.interp(
                np.linspace(0, len(audio_array), target_length),
                np.arange(len(audio_array)),
                audio_array,
            )

        return audio_array

    async def transcribe(
        self,
        audio_data: bytes = b"",
        language: str = "zh",
        sample_rate: int = 16000,
        audio: tuple = None,
    ) -> TranscriptionResult:
        """转写单段音频"""
        if not self._is_loaded:
            await self.load()

        if audio is not None:
            audio_array = audio[0]
        else:
            audio_array = self._convert_audio(audio_data, sample_rate)

        # VAD：静音检测 - 快速跳过空白录音
        rms = np.sqrt(np.mean(audio_array ** 2))
        if rms < _VAD_RMS_THRESHOLD:
            logger.debug(f"VAD: silence detected (RMS={rms:.5f}), skipping inference")
            return TranscriptionResult(
                text="",
                confidence=1.0,
                language=language,
                is_final=True,
            )

        try:
            start_time = time.time()

            # qwen_asr 的 transcribe 接口
            lang_param = language if language != "auto" else None

            # 直接调用，不使用 run_in_executor (减少开销)
            t0 = time.time()
            results = self._model.transcribe(
                audio=(audio_array, sample_rate),
                language=lang_param,
            )
            t1 = time.time()

            if results and len(results) > 0:
                text = results[0].text.strip()
                detected_lang = results[0].language
            else:
                text = ""
                detected_lang = language

            elapsed_ms = (time.time() - start_time) * 1000
            inference_ms = (t1 - t0) * 1000
            logger.info(
                f"[timing] qwen_asr.transcribe: {inference_ms:.0f}ms (total: {elapsed_ms:.0f}ms)"
            )

            return TranscriptionResult(
                text=text,
                confidence=1.0,
                language=detected_lang or language,
                is_final=True,
            )

        except Exception as e:
            logger.error(f"Transcription error: {e}")
            raise STTEngineError(f"Transcription failed: {e}")

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "zh",
        sample_rate: int = 16000,
    ) -> AsyncIterator[TranscriptionResult]:
        """流式转写（buffer 5 chunks 后逐段处理）"""
        if not self._is_loaded:
            await self.load()

        buffer = []
        async for chunk in audio_stream:
            buffer.append(chunk)
            if len(buffer) >= 5:
                combined = b"".join(buffer)
                result = await self.transcribe(
                    audio_data=combined,
                    language=language,
                    sample_rate=sample_rate,
                )
                if result.text.strip():
                    yield TranscriptionResult(
                        text=result.text.strip(),
                        confidence=result.confidence,
                        language=result.language,
                        is_final=False,
                    )
                buffer = []

        if buffer:
            combined = b"".join(buffer)
            result = await self.transcribe(
                audio_data=combined,
                language=language,
                sample_rate=sample_rate,
            )
            if result.text.strip():
                yield TranscriptionResult(
                    text=result.text.strip(),
                    confidence=result.confidence,
                    language=result.language,
                    is_final=True,
                )

    def get_model_info(self) -> dict:
        info = super().get_model_info()
        info.update(
            {
                "model_id": self.model_config.get("model_id", "unknown"),
                "description": self.model_config.get("description", ""),
                "device": str(self._device) if self._device else "unloaded",
                "quantize": self.model_config.get("quantize"),
                "use_bf16": self.model_config.get("use_bf16", True),
            }
        )
        return info
