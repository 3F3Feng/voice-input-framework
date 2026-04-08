"""
Voice Input Framework - STT 引擎抽象基类

定义所有 STT 引擎必须实现的接口。
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from voice_input_framework.shared.types import TranscriptionResult

logger = logging.getLogger(__name__)


class STTEngineError(Exception):
    """STT 引擎错误基类"""
    pass


class ModelNotLoadedError(STTEngineError):
    """模型未加载错误"""
    pass


class InferenceError(STTEngineError):
    """推理错误"""
    pass


@dataclass
class EngineStatus:
    """引擎状态"""
    name: str
    is_loaded: bool = False
    is_busy: bool = False
    model_size_mb: Optional[int] = None
    supported_languages: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "is_loaded": self.is_loaded,
            "is_busy": self.is_busy,
            "model_size_mb": self.model_size_mb,
            "supported_languages": self.supported_languages,
            "description": self.description,
        }


class BaseSTTEngine(ABC):
    """
    STT 引擎抽象基类

    所有 STT 引擎都必须继承此类并实现：
    - load_model: 加载模型
    - transcribe: 同步转写
    - unload_model: 卸载模型
    """

    def __init__(
        self,
        model_path: str,
        device: str = "auto",
        language: str = "auto",
        **kwargs
    ):
        self.model_path = model_path
        self.device = device
        self.language = language
        self.extra_params = kwargs
        self._model = None
        self._processor = None
        self._is_loaded = False
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    @abstractmethod
    async def load_model(self) -> None:
        """加载模型"""
        pass

    @abstractmethod
    async def unload_model(self) -> None:
        """卸载模型"""
        pass

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        language: Optional[str] = None,
        **kwargs
    ) -> TranscriptionResult:
        """
        同步转写

        Args:
            audio: 音频数据（PCM 格式）
            language: 语言提示

        Returns:
            TranscriptionResult: 转写结果
        """
        pass

    def get_status(self) -> EngineStatus:
        """获取引擎状态"""
        return EngineStatus(
            name=self.__class__.__name__,
            is_loaded=self._is_loaded,
            is_busy=self.is_busy,
            supported_languages=self._get_supported_languages(),
            description=self._get_description(),
        )

    @abstractmethod
    def _get_supported_languages(self) -> list[str]:
        """获取支持的语言列表"""
        pass

    @abstractmethod
    def _get_description(self) -> str:
        """获取引擎描述"""
        pass

    def _ensure_loaded(self) -> None:
        """确保模型已加载"""
        if not self._is_loaded:
            raise ModelNotLoadedError(
                f"Model {self.__class__.__name__} is not loaded. "
                "Call load_model() first."
            )

    async def __aenter__(self):
        await self.load_model()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.unload_model()
