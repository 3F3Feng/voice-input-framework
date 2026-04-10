#!/usr/bin/env python3
"""
Voice Input Framework - Qwen3-ASR 引擎实现
基于 Qwen3-ASR-1.7B 的语音识别引擎。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


class Qwen3ASREngine(BaseSTTEngine):
    """Qwen3-ASR STT 引擎"""

    MODEL_CONFIGS = {
        "qwen_asr": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "memory_gb": 3.5,
        },
        "qwen_asr_small": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "memory_gb": 1.5,
        },
    }

    def __init__(self, model_name: str = "qwen_asr", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model = None
        self.model_config = self.MODEL_CONFIGS.get(model_name, self.MODEL_CONFIGS["qwen_asr"])

    async def load(self) -> None:
        if self._is_loaded:
            return
        logger.info(f"Loading Qwen3-ASR model: {self.model_config['model_id']}")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_sync)
            self._is_loaded = True
        except Exception as e:
            raise STTEngineError(f"Failed to load model: {e}")

    def _load_sync(self):
        import torch
        from qwen_asr import Qwen3ASRModel
        
        model_id = self.model_config["model_id"]
        device = self.detect_device()
        
        # Qwen3-ASR 使用 bfloat16 效果更好
        dtype = torch.bfloat16 if device != "cpu" else torch.float32
        logger.info(f"Loading model on {device} with {dtype}")
        
        self._model = Qwen3ASRModel.from_pretrained(
            model_id,
            dtype=dtype,
            device_map=device,
            max_new_tokens=256,
        )

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._model = None
        self._is_loaded = False
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _convert_audio(self, audio_data: bytes, sample_rate: int = 16000):
        """将音频数据转换为 numpy 数组"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_array = audio_array.astype(np.float32) / 32768.0
        return audio_array, sample_rate

    async def transcribe(
        self, audio_data: bytes, language: str = "auto", sample_rate: int = 16000,
    ) -> TranscriptionResult:
        if not self._is_loaded:
            await self.load()

        loop = asyncio.get_event_loop()
        audio_array, sr = self._convert_audio(audio_data, sample_rate)

        def _do():
            # Qwen3-ASR 使用 (np.ndarray, sample_rate) 元组作为输入
            # language=None 表示自动检测语言
            lang = None if language == "auto" else language
            results = self._model.transcribe(
                audio=(audio_array, sr),
                language=lang,
            )
            if results and len(results) > 0:
                return results[0].text, results[0].language
            return "", language

        text, detected_lang = await loop.run_in_executor(None, _do)
        text = text.strip()

        return TranscriptionResult(
            text=text,
            confidence=1.0,
            language=detected_lang or language,
            is_final=True,
        )

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes], language: str = "auto", sample_rate: int = 16000,
    ) -> AsyncIterator[TranscriptionResult]:
        if not self._is_loaded:
            await self.load()

        buffer = []
        async for chunk in audio_stream:
            buffer.append(chunk)
            if len(buffer) >= 5:
                combined = b"".join(buffer)
                result = await self.transcribe(combined, language, sample_rate)
                if result.text:
                    yield result
                buffer = []

        if buffer:
            combined = b"".join(buffer)
            result = await self.transcribe(combined, language, sample_rate)
            if result.text:
                yield result
