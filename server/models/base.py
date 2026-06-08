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

        # 如果 device 是 "auto"，自动检测最优设备
        if device == "auto":
            self.device = self.detect_device()

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
        """自动检测可用设备

        使用统一平台检测模块，返回最优设备:
        - "cuda": NVIDIA GPU
        - "mps": Apple Silicon GPU (Metal Performance Shaders)
        - "cpu": CPU (无 GPU 加速)

        注意: MLX 引擎不使用此方法，它们有自己的加载逻辑
        """
        try:
            from shared.platform_detector import detect_platform

            platform_info = detect_platform()

            if platform_info.has_cuda:
                return "cuda"
            elif platform_info.has_mps:
                return "mps"
            else:
                return "cpu"
        except ImportError:
            # 降级到 torch 检测
            try:
                import torch

                if torch.cuda.is_available():
                    return "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    return "mps"
                else:
                    return "cpu"
            except ImportError:
                return "cpu"

    @staticmethod
    def get_platform_info() -> dict:
        """获取平台信息（用于日志和调试）"""
        try:
            from shared.platform_detector import detect_platform

            platform_info = detect_platform()
            return {
                "system": platform_info.system,
                "arch": platform_info.arch,
                "backend": platform_info.best_backend,
                "has_cuda": platform_info.has_cuda,
                "has_mlx": platform_info.has_mlx,
                "has_mps": platform_info.has_mps,
                "gpu": platform_info.gpu_info,
            }
        except ImportError:
            return {"error": "platform_detector not available"}

    def get_model_info(self) -> dict:
        """获取模型信息"""
        return {
            "name": self.model_name,
            "device": self.device,
            "is_loaded": self._is_loaded,
            "platform": self.get_platform_info(),
        }


# 前向引用避免循环导入
from shared.data_types import TranscriptionResult  # noqa: E402
