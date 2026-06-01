#!/usr/bin/env python3
"""
Voice Input Framework - MLX LLM 引擎

Apple Silicon 优化的 LLM 引擎，使用 mlx-lm 库。
仅在 Apple Silicon (MLX 可用) 时可用。
"""

import asyncio
import logging
from typing import Optional

from server.llm_engines.base import BaseLLMEngine, LLMEngineError

logger = logging.getLogger(__name__)


class MLXLLMEngine(BaseLLMEngine):
    """
    MLX LLM 引擎
    
    使用 mlx-lm 库在 Apple Silicon 上运行 LLM 模型。
    支持 MLX 量化模型 (4bit/8bit)。
    """
    
    def __init__(self, model_name: str, model_id: str, **kwargs):
        super().__init__(model_name, model_id, **kwargs)
        self._model = None
        self._tokenizer = None
    
    async def load(self) -> bool:
        """加载 MLX 模型"""
        if self._is_loaded:
            return True
        
        if self._is_loading:
            logger.info("MLX model is loading, waiting...")
            while self._is_loading:
                await asyncio.sleep(0.5)
            return self._is_loaded
        
        self._is_loading = True
        
        try:
            logger.info(f"Loading MLX LLM model: {self.model_id}")
            
            # 在线程池中加载模型（MLX 需要在主线程）
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, self._load_sync)
            
            if success:
                self._is_loaded = True
                logger.info(f"MLX LLM model loaded: {self.model_name}")
            else:
                logger.error(f"Failed to load MLX LLM model: {self.model_name}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error loading MLX LLM model: {e}")
            raise LLMEngineError(f"Failed to load MLX model: {e}")
        finally:
            self._is_loading = False
    
    def _load_sync(self) -> bool:
        """同步加载模型"""
        try:
            import mlx_lm
            self._model, self._tokenizer = mlx_lm.load(self.model_id)
            return True
        except ImportError:
            logger.error("mlx-lm not installed. Run: pip install mlx-lm")
            return False
        except Exception as e:
            logger.error(f"MLX load error: {e}")
            return False
    
    async def unload(self) -> None:
        """卸载模型"""
        if self._model is not None:
            del self._model
            self._model = None
        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None
        
        self._is_loaded = False
        logger.info(f"MLX LLM model unloaded: {self.model_name}")
    
    async def generate(self, prompt: str, max_tokens: int = 256) -> str:
        """生成文本"""
        if not self._is_loaded:
            raise LLMEngineError("Model not loaded")
        
        try:
            import mlx_lm
            
            # 在线程池中生成
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: mlx_lm.generate(
                    model=self._model,
                    tokenizer=self._tokenizer,
                    prompt=prompt,
                    max_tokens=max_tokens,
                )
            )
            
            return response
            
        except Exception as e:
            logger.error(f"MLX generate error: {e}")
            raise LLMEngineError(f"Generation failed: {e}")
    
    def get_info(self) -> dict:
        """获取引擎信息"""
        info = super().get_info()
        info["backend"] = "mlx"
        info["requires"] = "Apple Silicon + mlx-lm"
        return info


def is_available() -> bool:
    """检查 MLX 引擎是否可用"""
    try:
        import platform
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            return False
        import mlx_lm
        return True
    except ImportError:
        return False
