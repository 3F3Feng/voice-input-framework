#!/usr/bin/env python3
"""
Voice Input Framework - 真正的 MLX 原生引擎

使用 mlx-audio 0.4.2 原生支持，加载 mlx-community 量化模型。
相比 PyTorch 版：内存更低、Apple Silicon 优化、支持 4-bit/8-bit 量化。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


class Qwen3ASRMLXNativeEngine(BaseSTTEngine):
    """真正的 MLX 原生 Qwen3-ASR 引擎"""

    MODEL_CONFIGS = {
        "qwen_asr_mlx_native": {
            "model_id": "mlx-community/Qwen3-ASR-1.7B-8bit",
            "memory_gb": 1.0,  # 8-bit 量化，大幅降低
            "description": "Qwen3-ASR-1.7B MLX 8bit (推荐)",
        },
        "qwen_asr_mlx_native_small": {
            "model_id": "mlx-community/Qwen3-ASR-0.6B-4bit",
            "memory_gb": 0.5,  # 4-bit 量化
            "description": "Qwen3-ASR-0.6B MLX 4bit (更快、更省内存)",
        },
    }

    def __init__(self, model_name: str = "qwen_asr_mlx_native", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model = None
        self.model_config = self.MODEL_CONFIGS.get(
            model_name, self.MODEL_CONFIGS["qwen_asr_mlx_native"]
        )

    async def load(self) -> None:
        if self._is_loaded:
            return
        logger.info(f"Loading MLX model: {self.model_config['model_id']}")
        try:
            # MLX 模型必须在当前线程加载（Metal stream thread-local）
            self._load_sync()
            self._is_loaded = True
        except Exception as e:
            raise STTEngineError(f"Failed to load MLX model: {e}")

    def _load_sync(self):
        from mlx_audio.stt.utils import load_model

        model_id = self.model_config["model_id"]
        logger.info(f"Loading MLX model from {model_id}")
        self._model = load_model(model_id)

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._model = None
        self._is_loaded = False
        # MLX doesn't have explicit cache clearing like torch

    def _convert_audio(self, audio_data: bytes, sample_rate: int = 16000) -> np.ndarray:
        """将音频数据转换为一维 float32 numpy 数组 (16kHz mono)"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_array = audio_array.astype(np.float32) / 32768.0

        # 重采样到 16kHz（如果需要）
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
        language: str = "auto",
        sample_rate: int = 16000,
        audio: tuple = None,  # 兼容 stt_server 通用转发 (audio_array, sample_rate)
    ) -> TranscriptionResult:
        """转写音频

        Args:
            audio_data: 原始音频 bytes (16-bit PCM)
            language: 语言 ("auto" 自动检测)
            sample_rate: 原始采样率
            audio: (numpy_array, sr) 元组 — 兼容 stt_server 通用转发接口
        """
        if not self._is_loaded:
            await self.load()

        if audio is not None:
            # stt_server 通用转发：传入了预处理的 (array, sr)
            audio_array = audio[0]
        else:
            audio_array = self._convert_audio(audio_data, sample_rate)

        # MLX generate 直接在当前线程执行（MLX Metal 是 thread-local）
        lang = None if language == "auto" else language
        result = self._model.generate(
            audio=audio_array,
            language=lang,
            max_tokens=256,
            temperature=0.0,
        )
        text = result.text
        # MLX 返回的 language 可能是 ['English'] (list)，标准化为字符串
        detected_lang = result.language
        if isinstance(detected_lang, list):
            detected_lang = detected_lang[0] if detected_lang else ""
        if isinstance(detected_lang, str) and detected_lang == "None":
            detected_lang = ""
        text = text.strip()

        return TranscriptionResult(
            text=text,
            confidence=1.0,
            language=detected_lang or language,
            is_final=True,
        )

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> AsyncIterator[TranscriptionResult]:
        if not self._is_loaded:
            await self.load()

        # 流式：用 stream_transcribe 或 buffer 后逐段处理
        buffer = []
        async for chunk in audio_stream:
            buffer.append(chunk)
            if len(buffer) >= 5:
                combined = b"".join(buffer)
                audio_array = self._convert_audio(combined, sample_rate)
                result = self._model.generate(
                    audio=audio_array, language=None, max_tokens=256
                )
                if result and result.text.strip():
                    yield TranscriptionResult(
                        text=result.text.strip(),
                        confidence=1.0,
                        language=result.language or language,
                        is_final=False,
                    )
                buffer = []

        # 处理剩余 buffer
        if buffer:
            combined = b"".join(buffer)
            audio_array = self._convert_audio(combined, sample_rate)
            result = self._model.generate(
                audio=audio_array, language=None, max_tokens=256
            )
            if result and result.text.strip():
                yield TranscriptionResult(
                    text=result.text.strip(),
                    confidence=1.0,
                    language=result.language or language,
                    is_final=True,
                )

    def get_model_info(self) -> dict:
        info = super().get_model_info()
        info.update(
            {
                "model_id": self.model_config.get("model_id", "unknown"),
                "description": self.model_config.get("description", ""),
            }
        )
        return info


# 注册别名
MLXNativeQwen3ASREngine = Qwen3ASRMLXNativeEngine
