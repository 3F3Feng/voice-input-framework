#!/usr/bin/env python3
"""
Voice Input Framework - CUDA Qwen3-ASR 引擎 (4090 优化)

替代 MLX 版 qwen3_asr_mlx_native.py。
使用 Qwen ASR 官方包 + PyTorch CUDA 推理。

优化方案（按推荐优先级）:
1. Flash Attention 2 (-20-30% 推理时间)
2. int8 量化 (bitsandbytes load_in_8bit, 显存减半, 速度持平)
3. torch.compile (首次慢, 后续 -10-20%)
4. 更大的 Qwen ASR 模型 (可跑 Qwen2-Audio-7B+)

使用方式:
    在分离架构中: 启动 STT Service (端口 6544)
    默认模型: Qwen/Qwen3-ASR-1.7B (CUDA FP16)
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np
import torch

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


class Qwen3ASRCudaEngine(BaseSTTEngine):
    """CUDA 版 Qwen3-ASR 引擎 (RTX 4090 优化)"""

    MODEL_CONFIGS = {
        "qwen_asr": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "memory_gb": 3.5,   # FP16 VRAM 占用
            "dtype": torch.float16,
            "description": "Qwen3-ASR-1.7B CUDA FP16 (推荐)",
        },
        "qwen_asr_small": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "memory_gb": 1.5,
            "dtype": torch.float16,
            "description": "Qwen3-ASR-0.6B CUDA FP16 (更快)",
        },
    }

    def __init__(self, model_name: str = "qwen_asr", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model = None
        self._processor = None
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
            logger.info(
                f"Model loaded on {self._device}: {self.model_config['model_id']}"
            )
        except Exception as e:
            raise STTEngineError(f"Failed to load CUDA model: {e}")

    def _load_sync(self):
        """同步加载模型（CUDA tensor ops 必须在主线程）"""
        from transformers import AutoModelForCausalLM, AutoProcessor

        model_id = self.model_config["model_id"]

        if not torch.cuda.is_available():
            raise STTEngineError("CUDA is not available on this machine")

        self._device = torch.device("cuda:0")

        self._processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)

        # 尝试 Flash Attention 2（需要 flash-attn 安装）
        try:
            self._model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=self.model_config["dtype"],
                device_map="cuda:0",
                attn_implementation="flash_attention_2",
                trust_remote_code=True,
            )
            logger.info("Flash Attention 2 enabled")
        except (ImportError, ValueError) as e:
            logger.warning(f"Flash Attention 2 not available ({e}), falling back to sdpa")
            self._model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=self.model_config["dtype"],
                device_map="cuda:0",
                trust_remote_code=True,
            )

        # 验证模型参数在 GPU 上
        first_param_device = next(self._model.parameters()).device
        logger.info(f"Model parameters on: {first_param_device}")

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._model = None
        self._processor = None
        self._device = None
        torch.cuda.empty_cache()
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
        """转写单段音频

        Args:
            audio_data: 原始 PCM int16 bytes
            language: "zh" / "en" / "auto"
            sample_rate: 原始采样率
            audio: (np_array, sr) 兼容 stt_server 通用转发

        Returns:
            TranscriptionResult
        """
        if not self._is_loaded:
            await self.load()

        if audio is not None:
            audio_array = audio[0]
        else:
            audio_array = self._convert_audio(audio_data, sample_rate)

        lang_param = language if language != "auto" else None

        try:
            inputs = self._processor(
                audios=audio_array,
                sampling_rate=16000,
                return_tensors="pt",
                language=lang_param,
            ).to(self._device)

            with torch.no_grad():
                generated_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=256,
                    temperature=0.0,
                    do_sample=False,
                )

            text = self._processor.batch_decode(
                generated_ids, skip_special_tokens=True
            )[0]

            # 清理 ASR 输出前缀
            text = text.strip()
            for prefix in ["ASSISTANT: ", "assistant: ", "Assistant: "]:
                if text.startswith(prefix):
                    text = text[len(prefix):]
                    break

            return TranscriptionResult(
                text=text,
                confidence=1.0,
                language=language,
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
