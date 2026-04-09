"""
Voice Input Framework - STT 引擎管理器

管理多个 STT 引擎的加载、切换和调用。
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Type

from server.config import ServerConfig
from server.models import AVAILABLE_MODELS, BaseSTTEngine
from shared.data_types import ModelInfo, TranscriptionResult

logger = logging.getLogger(__name__)


class STTEngineManager:
    """STT 引擎管理器"""
    
    def __init__(self, config: ServerConfig):
        self.config = config
        self.engines: Dict[str, BaseSTTEngine] = {}
        self.current_model_name: str = config.default_model
        self._lock = asyncio.Lock()
        self._loading_models: Dict[str, float] = {}  # 正在加载的模型及开始时间
    
    async def initialize(self) -> None:
        """初始化管理器，加载默认模型"""
        if self.config.auto_load_default:
            try:
                await self.load_model(self.current_model_name)
                logger.info(f"Default model loaded: {self.current_model_name}")
            except Exception as e:
                logger.warning(f"Failed to load default model: {e}")
    
    async def load_model(self, model_name: str) -> None:
        """加载指定模型（外部调用，需要锁）"""
        async with self._lock:
            await self._load_model_unlocked(model_name)
    
    async def _load_model_unlocked(self, model_name: str) -> None:
        """内部加载模型方法（假设已经持有锁）"""
        if model_name in self.engines:
            logger.info(f"Model {model_name} already loaded")
            return
        
        if model_name not in AVAILABLE_MODELS:
            raise ValueError(f"Unknown model: {model_name}")
        
        engine_class = AVAILABLE_MODELS[model_name]
        engine = engine_class(model_name=model_name)
        
        try:
            await engine.load()
            self.engines[model_name] = engine
            logger.info(f"Model loaded: {model_name}")
        except Exception as e:
            logger.error(f"Failed to load model {model_name}: {e}")
            raise
    
    async def switch_model(self, model_name: str) -> None:
        """切换当前模型"""
        logger.info(f"switch_model called with: {model_name}")
        
        async with self._lock:
            # 如果正在加载，等待加载完成
            if model_name in self._loading_models:
                logger.info(f"Model {model_name} is still loading, waiting...")
                start_time = self._loading_models[model_name]
                while model_name in self._loading_models:
                    if time.time() - start_time > 300:  # 5分钟超时
                        raise TimeoutError(f"Model {model_name} loading timeout")
                    await asyncio.sleep(1)
                
                if model_name in self.engines:
                    self.current_model_name = model_name
                    return
            
            if model_name not in self.engines:
                logger.info(f"Model {model_name} not in engines, loading...")
                # 标记为正在加载
                self._loading_models[model_name] = time.time()
                try:
                    await self._load_model_unlocked(model_name)
                    logger.info(f"Model {model_name} loaded successfully")
                finally:
                    self._loading_models.pop(model_name, None)
            else:
                logger.info(f"Model {model_name} already in engines")
            
            # Verify the model is now loaded
            if model_name not in self.engines:
                raise ValueError(f"Model {model_name} failed to load")
            
            self.current_model_name = model_name
            logger.info(f"Switched to model: {model_name}, current_model_name is now: {self.current_model_name}")
    
    async def get_current_engine(self) -> Optional[BaseSTTEngine]:
        """获取当前引擎"""
        # 如果模型正在加载中，等待
        if self.current_model_name in self._loading_models:
            logger.info(f"Model {self.current_model_name} is still loading, waiting...")
            start_time = self._loading_models[self.current_model_name]
            while self.current_model_name in self._loading_models:
                if time.time() - start_time > 300:  # 5分钟超时
                    raise TimeoutError(f"Model {self.current_model_name} loading timeout")
                await asyncio.sleep(1)
        
        if self.current_model_name not in self.engines:
            await self.load_model(self.current_model_name)
        return self.engines.get(self.current_model_name)
    
    def is_model_loading(self, model_name: str) -> bool:
        """检查模型是否正在加载"""
        return model_name in self._loading_models
    
    def get_model_loading_time(self, model_name: str) -> Optional[float]:
        """获取模型开始加载的时间"""
        return self._loading_models.get(model_name)
    
    async def transcribe(
        self, 
        audio_data: bytes, 
        model_name: Optional[str] = None
    ) -> TranscriptionResult:
        """转写音频"""
        model = model_name or self.current_model_name
        
        if model not in self.engines:
            await self.load_model(model)
        
        engine = self.engines[model]
        return await engine.transcribe(audio_data)
    
    async def list_models(self) -> List[ModelInfo]:
        """列出可用模型"""
        models = []
        for name, engine_class in AVAILABLE_MODELS.items():
            is_loaded = name in self.engines
            is_current = name == self.current_model_name
            
            info = ModelInfo(
                name=name,
                description=f"STT model: {name}",
                supported_languages=["zh", "en", "auto"],
                is_loaded=is_loaded,
                is_default=is_current,
            )
            models.append(info)
        return models
    
    async def shutdown(self) -> None:
        """关闭所有引擎"""
        for name, engine in list(self.engines.items()):
            try:
                await engine.unload()
                logger.info(f"Model unloaded: {name}")
            except Exception as e:
                logger.warning(f"Failed to unload {name}: {e}")
        self.engines.clear()
