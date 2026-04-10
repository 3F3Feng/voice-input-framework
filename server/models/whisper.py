"""
Voice Input Framework - Whisper STT 引擎实现

基于 transformers 库的 Whisper 模型实现。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Optional

import numpy as np
import torch
from transformers import pipeline

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult
from shared.audio_confidence import estimate_confidence

logger = logging.getLogger(__name__)


class WhisperEngine(BaseSTTEngine):
    """Whisper STT 引擎"""

    MODEL_CONFIGS = {
        "whisper-large-v3": {
            "name": "openai/whisper-large-v3",
            "memory_gb": 10,
        },
        "whisper-small": {
            "name": "openai/whisper-small",
            "memory_gb": 2,
        },
    }

    def __init__(self, model_name: str = "whisper-large-v3", **kwargs):
        super().__init__(model_name, **kwargs)
        self._pipeline = None
        self.model_config = self.MODEL_CONFIGS.get(model_name, self.MODEL_CONFIGS["whisper-large-v3"])

    async def load(self) -> None:
        if self._is_loaded:
            return

        logger.info(f"Loading Whisper model: {self.model_name}")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_sync)
            self._is_loaded = True
        except Exception as e:
            raise STTEngineError(f"Failed to load model: {e}")

    def _load_sync(self):
        device = self.detect_device()
        dtype = torch.float16 if device == "cuda" else torch.float32

        self._pipeline = pipeline(
            "automatic-speech-recognition",
            model=self.model_config["name"],
            torch_dtype=dtype,
            device=device,
        )

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._pipeline = None
        self._is_loaded = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _convert_audio(self, audio_data: bytes) -> np.ndarray:
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        return audio_array.astype(np.float32) / 32768.0

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        if not self._is_loaded:
            await self.load()

        loop = asyncio.get_event_loop()
        audio_array = self._convert_audio(audio_data)

        def _do():
            return self._pipeline(
                audio_array,
                generate_kwargs={"language": language if language != "auto" else None},
            )

        result = await loop.run_in_executor(None, _do)
        text = result.get("text", "").strip()
        
        # 估算置信度
        confidence = estimate_confidence(audio_data, text, sample_rate)

        return TranscriptionResult(
            text=text,
            confidence=confidence,
            language=language,
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

        buffer = []
        async for chunk in audio_stream:
            buffer.append(chunk)
            if len(buffer) >= 10:
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
