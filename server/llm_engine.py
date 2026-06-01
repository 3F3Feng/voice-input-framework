#!/usr/bin/env python3
"""
Voice Input Framework - 统一 LLM 引擎管理器

支持多后端的 LLM 引擎管理:
- MLX: Apple Silicon (mlx-lm)
- CUDA: NVIDIA GPU (transformers + bitsandbytes)
- CPU: 通用备选 (transformers, 有限功能)

自动检测平台并选择最优后端。
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any

from shared.platform_detector import detect_platform, PlatformInfo

logger = logging.getLogger(__name__)


# LLM 模型注册表
LLM_MODELS_CONFIG: Dict[str, Dict[str, Any]] = {
    # ── MLX 模型 (Apple Silicon) ──
    "Qwen3.5-4B-OptiQ": {
        "model_id": "mlx-community/Qwen3.5-4B-OptiQ-4bit",
        "backend": "mlx",
        "memory_gb": 0.8,
        "description": "Qwen3.5-4B MLX 4bit (推荐，平衡速度和精度)",
        "requires_mlx": True,
    },
    "Qwen3.5-2B-OptiQ": {
        "model_id": "mlx-community/Qwen3.5-2B-OptiQ-4bit",
        "backend": "mlx",
        "memory_gb": 2.0,
        "description": "Qwen3.5-2B MLX 4bit (速度更快)",
        "requires_mlx": True,
    },
    "Qwen3.5-4B-MLX": {
        "model_id": "mlx-community/Qwen3.5-4B-MLX-4bit",
        "backend": "mlx",
        "memory_gb": 4.0,
        "description": "Qwen3.5-4B MLX 标准量化",
        "requires_mlx": True,
    },
    "Qwen3-0.6B": {
        "model_id": "mlx-community/Qwen3-0.6B-4bit",
        "backend": "mlx",
        "memory_gb": 0.5,
        "description": "Qwen3-0.6B MLX 4bit (最小，最快)",
        "requires_mlx": True,
    },
    "Qwen3-1.7B": {
        "model_id": "mlx-community/Qwen3-1.7B-4bit",
        "backend": "mlx",
        "memory_gb": 1.5,
        "description": "Qwen3-1.7B MLX 4bit (中等)",
        "requires_mlx": True,
    },
    "Gemma-4-E4B-DECKARD": {
        "model_id": "nightmedia/gemma-4-E4B-it-The-DECKARD-V2-Strong-HERETIC-UNCENSORED-Instruct-mxfp8-mlx",
        "backend": "mlx",
        "memory_gb": 4.0,
        "description": "Gemma 4 4B MLX (Google, 中文较弱)",
        "requires_mlx": True,
    },
    # ── CUDA 模型 (NVIDIA GPU) ──
    "Qwen3.5-4B-CUDA": {
        "model_id": "Qwen/Qwen3.5-4B",
        "backend": "cuda",
        "memory_gb": 8.0,
        "dtype": "float16",
        "description": "Qwen3.5-4B CUDA FP16 (推荐，8GB VRAM)",
        "requires_cuda": True,
    },
    "Qwen3.5-2B-CUDA": {
        "model_id": "Qwen/Qwen3.5-2B",
        "backend": "cuda",
        "memory_gb": 4.0,
        "dtype": "float16",
        "description": "Qwen3.5-2B CUDA FP16 (4GB VRAM)",
        "requires_cuda": True,
    },
    "Qwen3.5-4B-CUDA-INT8": {
        "model_id": "Qwen/Qwen3.5-4B",
        "backend": "cuda",
        "memory_gb": 4.0,
        "dtype": "int8",
        "description": "Qwen3.5-4B CUDA int8 量化 (4GB VRAM)",
        "requires_cuda": True,
    },
    "Qwen3.5-2B-CUDA-INT8": {
        "model_id": "Qwen/Qwen3.5-2B",
        "backend": "cuda",
        "memory_gb": 2.0,
        "dtype": "int8",
        "description": "Qwen3.5-2B CUDA int8 量化 (2GB VRAM)",
        "requires_cuda": True,
    },
    "Qwen3.5-4B-CUDA-INT4": {
        "model_id": "Qwen/Qwen3.5-4B",
        "backend": "cuda",
        "memory_gb": 2.5,
        "dtype": "int4",
        "description": "Qwen3.5-4B CUDA int4 量化 (2.5GB VRAM)",
        "requires_cuda": True,
    },
}


class LLMEngine:
    """
    统一 LLM 引擎管理器
    
    自动检测平台并选择最优后端:
    - Apple Silicon + MLX -> MLX 引擎
    - NVIDIA CUDA GPU -> CUDA 引擎
    - 其他 -> 不支持（CPU 不推荐运行 LLM）
    """
    
    def __init__(self, platform_info: Optional[PlatformInfo] = None):
        self.platform = platform_info or detect_platform()
        self._engine = None
        self._current_model_name: str = ""
        self._is_processing = False
        self.start_time = time.time()
    
    @property
    def current_model(self) -> str:
        """获取当前模型名称"""
        return self._current_model_name
    
    @property
    def is_loaded(self) -> bool:
        """检查是否已加载模型"""
        return self._engine is not None and self._engine.is_loaded
    
    @property
    def is_processing(self) -> bool:
        """检查是否正在处理"""
        return self._is_processing
    
    def get_default_model(self) -> str:
        """获取当前平台的默认 LLM 模型"""
        if self.platform.has_mlx:
            return "Qwen3.5-4B-OptiQ"
        elif self.platform.has_cuda:
            if self.platform.cuda_device and self.platform.cuda_device.memory_gb >= 8:
                return "Qwen3.5-4B-CUDA"
            else:
                return "Qwen3.5-2B-CUDA"
        else:
            # CPU 不推荐运行 LLM
            logger.warning("No GPU acceleration available for LLM. CPU inference is not recommended.")
            return ""
    
    def get_available_models(self) -> Dict[str, Dict[str, Any]]:
        """获取当前平台可用的 LLM 模型"""
        available = {}
        
        for name, config in LLM_MODELS_CONFIG.items():
            # 检查平台要求
            if config.get("requires_mlx") and not self.platform.has_mlx:
                continue
            if config.get("requires_cuda") and not self.platform.has_cuda:
                continue
            
            # 检查 VRAM 要求
            if config.get("requires_cuda") and self.platform.cuda_device:
                if config["memory_gb"] > self.platform.cuda_device.memory_gb:
                    continue
            
            available[name] = config.copy()
            available[name]["recommended"] = (name == self.get_default_model())
        
        return available
    
    async def load_model(self, model_name: str) -> bool:
        """
        加载指定的 LLM 模型
        
        Args:
            model_name: 模型名称 (如 "Qwen3.5-4B-OptiQ")
            
        Returns:
            是否加载成功
        """
        # 检查模型是否存在
        if model_name not in LLM_MODELS_CONFIG:
            logger.error(f"Unknown LLM model: {model_name}")
            return False
        
        config = LLM_MODELS_CONFIG[model_name]
        
        # 如果已经加载了同样的模型，直接返回
        if self._current_model_name == model_name and self._engine and self._engine.is_loaded:
            logger.info(f"LLM model {model_name} already loaded")
            return True
        
        # 卸载旧模型
        if self._engine:
            await self._engine.unload()
            self._engine = None
        
        # 创建新引擎
        backend = config["backend"]
        model_id = config["model_id"]
        
        try:
            if backend == "mlx":
                from server.llm_engines.mlx_engine import MLXLLMEngine
                self._engine = MLXLLMEngine(model_name, model_id)
            elif backend == "cuda":
                from server.llm_engines.cuda_engine import CUDALLMEngine
                dtype = config.get("dtype", "float16")
                self._engine = CUDALLMEngine(model_name, model_id, dtype=dtype)
            else:
                logger.error(f"Unknown backend: {backend}")
                return False
            
            # 加载模型
            success = await self._engine.load()
            if success:
                self._current_model_name = model_name
                logger.info(f"LLM model loaded: {model_name} (backend: {backend})")
            else:
                self._engine = None
            
            return success
            
        except Exception as e:
            logger.error(f"Error loading LLM model {model_name}: {e}")
            self._engine = None
            return False
    
    async def unload(self):
        """卸载当前模型"""
        if self._engine:
            await self._engine.unload()
            self._engine = None
            self._current_model_name = ""
            logger.info("LLM model unloaded")
    
    async def process(self, text: str, system_prompt: str = "") -> tuple[str, float]:
        """
        处理文本
        
        Args:
            text: 输入文本
            system_prompt: 系统提示词
            
        Returns:
            (处理结果, 延迟ms)
        """
        if not self._engine or not self._engine.is_loaded:
            logger.warning("No LLM model loaded, returning original text")
            return text, 0.0
        
        try:
            self._is_processing = True
            
            result = await self._engine.process(text, system_prompt)
            
            logger.info(
                f"LLM processing: input={text[:30]}..., output={result.text[:30]}..., "
                f"latency={result.latency_ms:.0f}ms"
            )
            
            return result.text, result.latency_ms
            
        except Exception as e:
            logger.error(f"Error processing text: {e}")
            return text, -1
        finally:
            self._is_processing = False
    
    def get_info(self) -> dict:
        """获取引擎信息"""
        info = {
            "platform": {
                "system": self.platform.system,
                "arch": self.platform.arch,
                "backend": self.platform.best_backend,
                "gpu": self.platform.gpu_info,
            },
            "current_model": self._current_model_name,
            "is_loaded": self.is_loaded,
            "is_processing": self._is_processing,
            "uptime_seconds": time.time() - self.start_time,
        }
        
        if self._engine:
            info["engine"] = self._engine.get_info()
        
        return info


class LLMManager:
    """LLM 管理器（单例模式）"""
    
    _instance: Optional["LLMManager"] = None
    
    def __init__(self, platform_info: Optional[PlatformInfo] = None):
        self.platform = platform_info or detect_platform()
        self._engine: Optional[LLMEngine] = None
    
    @classmethod
    def get_instance(cls, platform_info: Optional[PlatformInfo] = None) -> "LLMManager":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls(platform_info)
        return cls._instance
    
    def get_engine(self) -> LLMEngine:
        """获取引擎实例"""
        if self._engine is None:
            self._engine = LLMEngine(self.platform)
        return self._engine
    
    async def load_default(self) -> bool:
        """加载默认模型"""
        engine = self.get_engine()
        default_model = engine.get_default_model()
        if default_model:
            return await engine.load_model(default_model)
        return False
    
    def get_available_models(self) -> Dict[str, Dict[str, Any]]:
        """获取可用模型列表"""
        return self.get_engine().get_available_models()
    
    async def unload(self):
        """卸载模型"""
        if self._engine:
            await self._engine.unload()
