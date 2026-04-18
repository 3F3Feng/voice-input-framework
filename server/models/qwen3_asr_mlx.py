#!/usr/bin/env python3
"""
Voice Input Framework - MLX 环境 Qwen3-ASR 引擎实现

解决 qwen-asr 包与 transformers 5.x 的兼容性问题，使用 MPS 后端运行。

方案：
1. 修补 transformers.utils.generic.check_model_inputs 的兼容性问题
2. 使用 qwen_asr 内部的 transformers_backend 加载模型
3. 支持 MPS（Apple Silicon）和 CPU
"""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from typing import Optional
from pathlib import Path

import numpy as np

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


def _patch_transformers_compatibility():
    """修补 transformers 5.x 与 qwen_asr 的兼容性问题"""
    import transformers.utils.generic as generic_module

    # 保存原始函数
    original_check_model_inputs = generic_module.check_model_inputs

    # 创建兼容的装饰器函数
    # 原始签名: check_model_inputs(func=None, *, tie_last_hidden_states=True)
    # 必须支持三种调用方式:
    # 1. @check_model_inputs  (无括号, func 是被装饰的函数)
    # 2. @check_model_inputs() (有括号, func=None, 返回装饰器)
    # 3. @check_model_inputs(tie_last_hidden_states=False) (带关键字参数)
    def compatible_check_model_inputs(func=None, *, tie_last_hidden_states=True):
        """兼容 qwen_asr 的 check_model_inputs 装饰器"""
        if func is not None:
            # 直接调用: @check_model_inputs
            # func 是被装饰的函数，直接传给原始函数
            return original_check_model_inputs(func, tie_last_hidden_states=tie_last_hidden_states)
        else:
            # 带括号调用: @check_model_inputs() 或 @check_model_inputs(tie_last_hidden_states=...)
            # 返回一个装饰器，它会调用原始函数
            def decorator(f):
                return original_check_model_inputs(f, tie_last_hidden_states=tie_last_hidden_states)
            return decorator

    # 替换模块中的函数
    generic_module.check_model_inputs = compatible_check_model_inputs
    sys.modules['transformers.utils.generic'].check_model_inputs = compatible_check_model_inputs
    logger.info("Patched transformers compatibility for qwen_asr")


class Qwen3ASRMLXEngine(BaseSTTEngine):
    """Qwen3-ASR MLX 环境引擎

    在 MLX conda 环境中运行 Qwen3-ASR，使用 MPS 或 CPU 后端。
    通过修补兼容性问题解决 transformers 版本冲突。
    """

    MODEL_CONFIGS = {
        "qwen_asr_mlx": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "memory_gb": 3.5,
            "description": "Qwen3-ASR-1.7B (推荐，更准确)",
        },
        "qwen_asr_mlx_small": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "memory_gb": 1.5,
            "description": "Qwen3-ASR-0.6B (更快)",
        },
    }

    def __init__(self, model_name: str = "qwen_asr_mlx", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model = None
        self._model_instance = None
        self.model_config = self.MODEL_CONFIGS.get(model_name, self.MODEL_CONFIGS["qwen_asr_mlx"])
        self._patched = False

    async def load(self) -> None:
        """加载模型"""
        if self._is_loaded:
            return

        logger.info(f"Loading Qwen3-ASR model: {self.model_config['model_id']}")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_sync)
            self._is_loaded = True
            logger.info("Qwen3-ASR model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise STTEngineError(f"Failed to load Qwen3-ASR model: {e}")

    def _load_sync(self):
        """同步加载模型"""
        import torch

        # 应用兼容性补丁
        if not self._patched:
            _patch_transformers_compatibility()
            self._patched = True

        # 现在可以安全导入 qwen_asr
        from qwen_asr import Qwen3ASRModel

        model_id = self.model_config["model_id"]

        # 检测设备
        if torch.backends.mps.is_available():
            device = "mps"
            dtype = torch.float32  # MPS 不完全支持 bfloat16
        elif torch.cuda.is_available():
            device = "cuda"
            dtype = torch.bfloat16
        else:
            device = "cpu"
            dtype = torch.float32

        logger.info(f"Loading model on {device} with {dtype}")

        self._model_instance = Qwen3ASRModel.from_pretrained(
            model_id,
            dtype=dtype,
            device_map=device,
            max_new_tokens=256,
        )
        self._model = self._model_instance

    async def unload(self) -> None:
        """卸载模型"""
        if not self._is_loaded:
            return

        self._model = None
        self._model_instance = None
        self._is_loaded = False

        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _convert_audio(self, audio_data: bytes, sample_rate: int = 16000) -> tuple:
        """将音频数据转换为 numpy 数组"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_array = audio_array.astype(np.float32) / 32768.0

        # 如果需要重采样（简单线性插值）
        if sample_rate != 16000:
            target_length = int(len(audio_array) * 16000 / sample_rate)
            audio_array = np.interp(
                np.linspace(0, len(audio_array), target_length),
                np.arange(len(audio_array)),
                audio_array,
            )

        return audio_array, 16000

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        """转写音频"""
        if not self._is_loaded:
            await self.load()

        loop = asyncio.get_event_loop()
        audio_array, sr = self._convert_audio(audio_data, sample_rate)

        def _do_transcribe():
            # Qwen3-ASR 使用 (np.ndarray, sample_rate) 元组作为输入
            lang = None if language == "auto" else language
            results = self._model.transcribe(
                audio=(audio_array, sr),
                language=lang,
            )
            if results and len(results) > 0:
                return results[0].text, results[0].language
            return "", language

        text, detected_lang = await loop.run_in_executor(None, _do_transcribe)
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
        """流式转写"""
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

    def get_model_info(self) -> dict:
        """获取模型信息"""
        info = super().get_model_info()
        info.update({
            "model_id": self.model_config.get("model_id", "unknown"),
            "description": self.model_config.get("description", ""),
        })
        return info


# 用于注册的别名
MLXQwen3ASREngine = Qwen3ASRMLXEngine
