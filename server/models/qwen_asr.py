"""
Voice Input Framework - Qwen ASR 模型实现

使用 ModelScope 加载和运行 Qwen-Audio/Qwen3-ASR 模型。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Optional

import torch
from modelscope import snapshot_download
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks

from voice_input_framework.server.models.base import (
    BaseSTTEngine,
    InferenceError,
    ModelNotLoadedError,
)
from voice_input_framework.shared.types import TranscriptionResult

logger = logging.getLogger(__name__)


class QwenASREngine(BaseSTTEngine):
    """
    Qwen ASR STT 引擎

    基于阿里 Qwen-Audio/Qwen2-Audio 的语音识别实现。

    Attributes:
        model_path: 模型路径或 ModelScope ID
        device: 运行设备
        language: 默认语言
    """

    def __init__(
        self,
        model_path: str = "qwen/Qwen2-Audio-7B-Instruct",
        device: str = "auto",
        language: str = "zh",
        **kwargs
    ):
        """
        初始化 Qwen ASR 引擎

        Args:
            model_path: 模型路径或 ID
            device: 运行设备
            language: 默认语言
            **kwargs: 额外参数
        """
        super().__init__(model_path, device, language, **kwargs)
        self._pipeline = None

    def _get_supported_languages(self) -> list[str]:
        """获取支持的语言列表"""
        return ["zh", "en", "ja", "ko"]

    def _get_description(self) -> str:
        """获取引擎描述"""
        return f"Qwen Audio ASR ({self.model_path}) - Alibaba's advanced audio-language model"

    async def load_model(self) -> None:
        """
        加载 Qwen 模型
        """
        if self._is_loaded:
            return

        logger.info(f"Loading Qwen ASR model: {self.model_path}")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_model_sync)
            self._is_loaded = True
            logger.info("Qwen ASR model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Qwen ASR: {e}")
            raise ModelNotLoadedError(f"Failed to load Qwen ASR: {e}")

    def _load_model_sync(self) -> None:
        """同步加载模型"""
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device != "auto":
            device = self.device

        # 使用 ModelScope pipeline
        self._pipeline = pipeline(
            task=Tasks.auto_speech_recognition,
            model=self.model_path,
            device=device,
        )

    async def unload_model(self) -> None:
        """卸载模型"""
        if not self._is_loaded:
            return

        self._pipeline = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        self._is_loaded = False
        logger.info("Qwen ASR model unloaded")

    async def transcribe(
        self,
        audio: bytes,
        language: Optional[str] = None,
        **kwargs
    ) -> TranscriptionResult:
        """同步转写"""
        self._ensure_loaded()

        try:
            # Qwen pipeline 通常接受文件路径或 wav 格式的 bytes
            # 这里我们简单地传递 bytes，ModelScope pipeline 会处理
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._pipeline(audio)
            )

            text = result.get("text", "") if isinstance(result, dict) else str(result)

            return TranscriptionResult(
                text=text,
                confidence=1.0,
                language=language or self.language,
                is_final=True,
            )
        except Exception as e:
            logger.error(f"Qwen transcription failed: {e}")
            raise InferenceError(f"Qwen transcription failed: {e}")
