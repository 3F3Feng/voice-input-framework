#!/usr/bin/env python3
"""
Voice Input Framework - CUDA Qwen3-ASR 引擎 (4090 优化)

使用 Qwen ASR 官方包 (qwen_asr) 进行推理。
支持 Flash Attention 2 和批量推理。

使用方式:
    在分离架构中: 启动 STT Service (端口 6544)
    默认模型: Qwen/Qwen3-ASR-1.7B (CUDA FP16)
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


class Qwen3ASRCudaEngine(BaseSTTEngine):
    """CUDA 版 Qwen3-ASR 引擎 (RTX 4090 优化)"""

    MODEL_CONFIGS = {
        "qwen_asr": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "memory_gb": 3.5,
            "description": "Qwen3-ASR-1.7B CUDA (推荐)",
        },
        "qwen_asr_small": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "memory_gb": 1.5,
            "description": "Qwen3-ASR-0.6B CUDA (更快)",
        },
    }

    def __init__(self, model_name: str = "qwen_asr", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model = None
        self.model_config = self.MODEL_CONFIGS.get(
            model_name, self.MODEL_CONFIGS["qwen_asr"]
        )
        self._device = None

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

        if not torch.cuda.is_available():
            raise STTEngineError("CUDA is not available on this machine")

        self._device = "cuda:0"

        # 使用 qwen_asr 官方包加载
        from qwen_asr import Qwen3ASRModel

        logger.info(f"Loading with qwen_asr package: {model_id}")

        # 尝试 Flash Attention 2
        try:
            self._model = Qwen3ASRModel.from_pretrained(
                model_id,
                dtype=torch.float16,
                device_map=self._device,
                attn_implementation="flash_attention_2",
                max_inference_batch_size=32,
                max_new_tokens=256,
            )
            logger.info("Flash Attention 2 enabled")
        except (ImportError, ValueError, RuntimeError) as e:
            logger.warning(f"Flash Attention 2 not available ({e}), falling back to default")
            self._model = Qwen3ASRModel.from_pretrained(
                model_id,
                dtype=torch.float16,
                device_map=self._device,
                max_inference_batch_size=32,
                max_new_tokens=256,
            )

        logger.info(f"Model loaded on {self._device}")

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._model = None
        self._device = None
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

        try:
            # qwen_asr 的 transcribe 接口
            lang_param = language if language != "auto" else None
            
            results = self._model.transcribe(
                audio=(audio_array, sample_rate),
                language=lang_param,
            )

            if results and len(results) > 0:
                text = results[0].text.strip()
                detected_lang = results[0].language
            else:
                text = ""
                detected_lang = language

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
        info.update({
            "model_id": self.model_config.get("model_id", "unknown"),
            "description": self.model_config.get("description", ""),
            "device": str(self._device) if self._device else "unloaded",
        })
        return info
