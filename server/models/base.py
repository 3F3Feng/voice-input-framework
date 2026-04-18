"""
Voice Input Framework - STT 引擎基类

定义所有 STT 引擎必须实现的接口。
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator
import asyncio
import logging

logger = logging.getLogger(__name__)


class STTEngineError(Exception):
    """STT 引擎相关错误"""
    pass


class BaseSTTEngine(ABC):
    """
    STT 引擎抽象基类
    
    所有 STT 模型实现必须继承此类并实现其方法。
    """
    
    def __init__(self, model_name: str, device: str = "auto"):
        self.model_name = model_name
        self.device = device
        self._model = None
        self._is_loaded = False
        self._lock = asyncio.Lock()
    
    @property
    def is_loaded(self) -> bool:
        return self._is_loaded
    
    @abstractmethod
    async def load(self) -> None:
        """加载模型"""
        pass
    
    @abstractmethod
    async def unload(self) -> None:
        """卸载模型"""
        pass
    
    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> "TranscriptionResult":
        """转写音频数据"""
        pass
    
    @abstractmethod
    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> AsyncIterator["TranscriptionResult"]:
        """流式转写音频流"""
        pass
    
    async def transcribe_with_lock(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> "TranscriptionResult":
        """带锁的转写（线程安全）"""
        async with self._lock:
            if not self._is_loaded:
                await self.load()
            return await self.transcribe(audio_data, language, sample_rate)
    
    @staticmethod
    def detect_device() -> str:
        """自动检测可用设备"""
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                return "mps"
            else:
                return "cpu"
        except ImportError:
            return "cpu"
    
    def get_model_info(self) -> dict:
        """获取模型信息"""
        return {
            "name": self.model_name,
            "device": self.device,
            "is_loaded": self._is_loaded,
        }


# 前向引用避免循环导入
from shared.data_types import TranscriptionResult
