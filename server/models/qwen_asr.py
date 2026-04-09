"""
Voice Input Framework - Qwen2-Audio 引擎实现

基于 Qwen2-Audio 的语音识别引擎。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np
import torch

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


class QwenASREngine(BaseSTTEngine):
    """Qwen2-Audio STT 引擎"""

    MODEL_CONFIGS = {
        "qwen_asr": {
            "model_id": "Qwen/Qwen2-Audio-7B-Instruct",
            "memory_gb": 5,
        },
    }

    def __init__(self, model_name: str = "qwen_asr", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model = None
        self._processor = None
        self.model_config = self.MODEL_CONFIGS.get(model_name, self.MODEL_CONFIGS["qwen_asr"])

    async def load(self) -> None:
        if self._is_loaded:
            return

        logger.info(f"Loading Qwen2-Audio model: {self.model_config['model_id']}")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_sync)
            self._is_loaded = True
        except Exception as e:
            raise STTEngineError(f"Failed to load model: {e}")

    def _load_sync(self):
        from transformers import Qwen2AudioForConditionalGeneration, Qwen2AudioProcessor
        
        model_id = self.model_config["model_id"]
        device = self.detect_device()
        dtype = torch.float16 if device == "cuda" else torch.float32

        logger.info(f"Loading model on {device} with {dtype}")
        
        self._processor = Qwen2AudioProcessor.from_pretrained(model_id)
        self._model = Qwen2AudioForConditionalGeneration.from_pretrained(
            model_id,
            torch_dtype=dtype,
        )
        self._model = self._model.to(device)

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._model = None
        self._processor = None
        self._is_loaded = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _convert_audio(self, audio_data: bytes, sample_rate: int = 16000):
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_array = audio_array.astype(np.float32) / 32768.0
        return audio_array

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        if not self._is_loaded:
            await self.load()

        loop = asyncio.get_event_loop()
        audio_array = self._convert_audio(audio_data, sample_rate)

        def _do():
            # Qwen2Audio 是多模态模型，需要同时提供文本和音频
            # 文本提示用于指导模型进行转录任务
            text_prompt = "Transcribe the audio."
            
            # 处理音频和文本
            inputs = self._processor(
                text=text_prompt,
                audio=audio_array,
                sampling_rate=sample_rate,
                return_tensors="pt",
                padding='longest',
            )
            inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
            
            with torch.no_grad():
                generated_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=256,
                )
            
            return self._processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

        text = await loop.run_in_executor(None, _do)
        text = text.strip()

        return TranscriptionResult(
            text=text,
            confidence=1.0,
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
