"""
Voice Input Framework - STT 引擎管理器

管理多个 STT 引擎的加载、切换和调用。
"""

import asyncio
import logging
from typing import Dict, List, Optional, Type

from voice_input_framework.server.config import ServerConfig
from voice_input_framework.server.models import AVAILABLE_MODELS, BaseSTTEngine
from voice_input_framework.shared.types import ModelInfo, TranscriptionResult

logger = logging.getLogger(__name__)


class STTEngineManager:
    """
    STT 引擎管理器
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        self.engines: Dict[str, BaseSTTEngine] = {}
        self.current_model_name: str = config.default_model
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """初始化管理器，加载默认模型"""
        if self.config.auto_load_default:
            await self.get_engine(self.config.default_model)

    async def get_engine(self, model_name: str) -> BaseSTTEngine:
        """获取或加载指定的引擎"""
        async with self._lock:
            if model_name in self.engines:
                return self.engines[model_name]

            if model_name not in AVAILABLE_MODELS:
                raise ValueError(f"Model {model_name} not found in available models")

            model_config = self.config.get_model_config(model_name)
            if not model_config:
                # 使用默认配置
                from voice_input_framework.server.config import ModelConfig
                model_config = ModelConfig(name=model_name, model_path=model_name)

            engine_class = AVAILABLE_MODELS[model_name]
            engine = engine_class(
                model_path=model_config.model_path,
                device=model_config.device,
                language=model_config.language,
                **model_config.extra
            )

            await engine.load_model()
            self.engines[model_name] = engine
            return engine

    async def switch_model(self, model_name: str) -> None:
        """切换当前使用的模型"""
        if model_name not in AVAILABLE_MODELS:
            raise ValueError(f"Model {model_name} not available")

        await self.get_engine(model_name)
        self.current_model_name = model_name
        logger.info(f"Switched to model: {model_name}")

    async def get_current_engine(self) -> BaseSTTEngine:
        """获取当前正在使用的引擎"""
        return await self.get_engine(self.current_model_name)

    async def list_models(self) -> List[ModelInfo]:
        """列出所有可用模型的信息"""
        model_infos = []
        for name in AVAILABLE_MODELS:
            is_loaded = name in self.engines
            is_default = name == self.config.default_model

            # 尝试获取引擎实例以获取更多信息
            desc = ""
            langs = []
            if is_loaded:
                status = self.engines[name].get_status()
                desc = status.description
                langs = status.supported_languages
            else:
                desc = f"{name} model"
                langs = ["auto"]

            model_infos.append(ModelInfo(
                name=name,
                description=desc,
                supported_languages=langs,
                is_loaded=is_loaded,
                is_default=is_default
            ))
        return model_infos

    async def transcribe(self, audio: bytes, model_name: Optional[str] = None) -> TranscriptionResult:
        """调用引擎进行转写"""
        engine = await self.get_engine(model_name or self.current_model_name)
        return await engine.transcribe(audio)

    async def shutdown(self) -> None:
        """关闭所有引擎，释放资源"""
        for name, engine in self.engines.items():
            logger.info(f"Unloading model: {name}")
            await engine.unload_model()
        self.engines.clear()
