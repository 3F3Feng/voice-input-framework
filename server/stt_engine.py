"""
Voice Input Framework - STT 引擎管理器

管理多个 STT 引擎的加载、切换和调用。
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional

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
        """初始化管理器，后台加载默认模型（非阻塞）"""
        if self.config.auto_load_default:
            try:
                # 后台加载，不阻塞启动
                if self.current_model_name not in self.engines and self.current_model_name not in self._loading_models:
                    self._loading_models[self.current_model_name] = time.time()
                    asyncio.create_task(self._background_load_model(self.current_model_name))
                    logger.info(f"Default model {self.current_model_name} loading in background")
                else:
                    logger.info(f"Default model {self.current_model_name} already loaded or loading")
            except Exception as e:
                logger.warning(f"Failed to start loading default model: {e}")
    
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
        """切换当前模型（非阻塞，立即返回）"""
        logger.info(f"switch_model called with: {model_name}")
        
        # If already loaded, switch immediately
        if model_name in self.engines:
            self.current_model_name = model_name
            logger.info(f"Model {model_name} already loaded, switched immediately")
            # Offload other models to save memory
            await self._offload_other_models(model_name)
            return
        
        # If currently loading, just set as current (will wait in get_current_engine)
        if model_name in self._loading_models:
            self.current_model_name = model_name
            logger.info(f"Model {model_name} is already loading, set as current")
            return
        
        # Start loading in background
        self._loading_models[model_name] = time.time()
        self.current_model_name = model_name
        logger.info(f"Model {model_name} loading started in background")
        
        # Fire and forget - load in background task
        asyncio.create_task(self._background_load_model(model_name))
    
    async def _offload_other_models(self, except_model: str) -> None:
        """卸载除指定模型外的所有模型"""
        models_to_offload = [m for m in self.engines.keys() if m != except_model]
        if models_to_offload:
            logger.info(f"Offloading models to free memory: {models_to_offload}")
            for old_model in models_to_offload:
                try:
                    await self.engines[old_model].unload()
                    logger.info(f"Offloaded model: {old_model}")
                except Exception as e:
                    logger.warning(f"Failed to offload {old_model}: {e}")
                finally:
                    del self.engines[old_model]
    
    async def _background_load_model(self, model_name: str) -> None:
        """后台加载模型（不阻塞，加载前自动 offload 其他模型以节省内存）"""
        try:
            async with self._lock:
                if model_name in self.engines:
                    logger.info(f"Model {model_name} already loaded (race)")
                    self._loading_models.pop(model_name, None)
                    return
                
                # Offload 其他模型（节省内存）
                models_to_offload = [m for m in self.engines.keys() if m != model_name]
                if models_to_offload:
                    logger.info(f"Offloading models to free memory: {models_to_offload}")
                    for old_model in models_to_offload:
                        if old_model in self.engines:
                            try:
                                await self.engines[old_model].unload()
                                logger.info(f"Offloaded model: {old_model}")
                            except Exception as e:
                                logger.warning(f"Failed to offload {old_model}: {e}")
                            finally:
                                del self.engines[old_model]
                
                engine_class = AVAILABLE_MODELS[model_name]
                engine = engine_class(model_name=model_name)
                
                logger.info(f"Background loading model {model_name}...")
                await engine.load()
                self.engines[model_name] = engine
                logger.info(f"Model {model_name} background loading complete")
        except Exception as e:
            logger.error(f"Background load failed for {model_name}: {e}")
        finally:
            self._loading_models.pop(model_name, None)
    
    async def ensure_model_loaded(self, model_name: str, timeout: float = 600.0) -> bool:
        """确保模型已加载，等待完成（如果正在加载）"""
        if model_name in self.engines:
            return True
        
        if model_name not in self._loading_models:
            # Model not loading, start loading
            await self.switch_model(model_name)
        
        # Wait for loading to complete
        start_time = time.time()
        while model_name in self._loading_models:
            if time.time() - start_time > timeout:
                raise TimeoutError(f"Model {model_name} loading timeout ({timeout}s)")
            await asyncio.sleep(1)
        
        return model_name in self.engines
    
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
