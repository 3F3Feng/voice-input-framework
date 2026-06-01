#!/usr/bin/env python3
"""
Voice Input Framework - 智能模型选择器

根据平台和硬件自动选择最佳模型，保证性能和资源平衡。

使用方式:
    from shared.model_selector import ModelSelector
    
    selector = ModelSelector()
    default_model = selector.get_default_model()
    available_models = selector.get_available_models()
"""

import logging
from typing import Dict, List, Any, Optional

from shared.platform_detector import detect_platform, PlatformInfo
from shared.model_registry import MODELS_CONFIG

logger = logging.getLogger(__name__)


class ModelSelector:
    """智能模型选择器"""
    
    def __init__(self, platform_info: Optional[PlatformInfo] = None):
        """
        初始化模型选择器
        
        Args:
            platform_info: 平台信息，如果为 None 则自动检测
        """
        self.platform = platform_info or detect_platform()
    
    def get_default_model(self) -> str:
        """返回当前平台的默认模型
        
        优先级:
        1. Apple Silicon + MLX -> qwen_asr_mlx_native_small
        2. NVIDIA CUDA GPU -> qwen_asr_cuda (显存>=8GB) 或 qwen_asr_cuda_small
        3. 其他 -> whisper_turbo (通用 CPU/MPS)
        
        Returns:
            模型名称
        """
        # Apple Silicon: 优先 MLX
        if self.platform.is_apple_silicon and self.platform.has_mlx:
            return "qwen_asr_mlx_native_small"
        
        # NVIDIA GPU: 优先 CUDA
        if self.platform.has_cuda:
            if self.platform.cuda_device and self.platform.cuda_device.memory_gb >= 8:
                return "qwen_asr_cuda"
            else:
                return "qwen_asr_cuda_small"
        
        # 其他平台: 通用模型
        return "whisper_turbo"
    
    def get_available_models(self) -> Dict[str, Dict[str, Any]]:
        """返回当前平台可用的模型列表（带推荐标记）
        
        Returns:
            字典，键为模型名称，值为模型配置（包含 recommended 标记）
        """
        available = {}
        
        for name, config in MODELS_CONFIG.items():
            # 检查平台要求
            if not self._is_model_available(name, config):
                continue
            
            # 复制配置并添加推荐标记
            model_info = config.copy()
            model_info["recommended"] = (name == self.get_default_model())
            model_info["available"] = True
            
            available[name] = model_info
        
        return available
    
    def get_recommended_models(self, top_n: int = 3) -> List[str]:
        """返回推荐的模型列表（按优先级排序）
        
        Args:
            top_n: 返回前 N 个推荐模型
            
        Returns:
            模型名称列表
        """
        recommendations = self.platform.get_recommended_stt_models()
        
        # 过滤掉当前平台不可用的模型
        available = [m for m in recommendations if m in MODELS_CONFIG and self._is_model_available(m, MODELS_CONFIG[m])]
        
        return available[:top_n]
    
    def filter_models(self, models: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """过滤掉当前平台不支持的模型
        
        Args:
            models: 模型配置字典
            
        Returns:
            过滤后的模型配置字典
        """
        filtered = {}
        
        for name, config in models.items():
            if self._is_model_available(name, config):
                filtered[name] = config
        
        return filtered
    
    def _is_model_available(self, name: str, config: Dict[str, Any]) -> bool:
        """检查模型是否在当前平台可用
        
        Args:
            name: 模型名称
            config: 模型配置
            
        Returns:
            是否可用
        """
        # 检查平台要求
        if config.get("requires_apple_silicon") and not self.platform.is_apple_silicon:
            return False
        if config.get("requires_mlx") and not self.platform.has_mlx:
            return False
        if config.get("requires_cuda") and not self.platform.has_cuda:
            return False
        if config.get("requires_macos") and not self.platform.is_macos:
            return False
        
        # 检查资源要求
        if config.get("memory_gb"):
            # 对于 CUDA 模型，检查 GPU 显存
            if config.get("requires_cuda") and self.platform.cuda_device:
                if config["memory_gb"] > self.platform.cuda_device.memory_gb:
                    logger.debug(f"Model {name} requires {config['memory_gb']}GB VRAM, "
                               f"but only {self.platform.cuda_device.memory_gb}GB available")
                    return False
        
        return True
    
    def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """获取模型详细信息
        
        Args:
            model_name: 模型名称
            
        Returns:
            模型信息字典，如果模型不存在或不可用则返回 None
        """
        if model_name not in MODELS_CONFIG:
            return None
        
        config = MODELS_CONFIG[model_name]
        
        if not self._is_model_available(model_name, config):
            return None
        
        # 添加额外信息
        info = config.copy()
        info["recommended"] = (model_name == self.get_default_model())
        info["available"] = True
        
        # 添加平台特定信息
        if config.get("requires_cuda") and self.platform.cuda_device:
            info["gpu_memory_required"] = f"{config['memory_gb']}GB"
            info["gpu_memory_available"] = f"{self.platform.cuda_device.memory_gb}GB"
        
        return info
    
    def get_platform_summary(self) -> Dict[str, Any]:
        """获取平台摘要信息（用于 API 响应）
        
        Returns:
            平台摘要字典
        """
        return {
            "system": self.platform.system,
            "arch": self.platform.arch,
            "backend": self.platform.best_backend,
            "gpu": self.platform.gpu_info,
            "has_mlx": self.platform.has_mlx,
            "has_cuda": self.platform.has_cuda,
            "has_mps": self.platform.has_mps,
            "recommended_stt": self.get_default_model(),
            "recommended_llm": self.platform.get_recommended_llm_models(),
            "available_models": list(self.get_available_models().keys()),
        }


# 全局缓存
_selector: Optional[ModelSelector] = None


def get_model_selector() -> ModelSelector:
    """获取全局模型选择器实例"""
    global _selector
    if _selector is None:
        _selector = ModelSelector()
    return _selector


def get_default_model() -> str:
    """获取当前平台的默认模型（便捷函数）"""
    return get_model_selector().get_default_model()


def get_available_models() -> Dict[str, Dict[str, Any]]:
    """获取当前平台可用的模型（便捷函数）"""
    return get_model_selector().get_available_models()


def get_recommended_models(top_n: int = 3) -> List[str]:
    """获取推荐的模型列表（便捷函数）"""
    return get_model_selector().get_recommended_models(top_n)


if __name__ == "__main__":
    # 测试模型选择器
    logging.basicConfig(level=logging.INFO)
    
    selector = ModelSelector()
    
    print("\n" + "=" * 60)
    print("Model Selector Test")
    print("=" * 60)
    
    print(f"\nPlatform: {selector.platform.system} {selector.platform.arch}")
    print(f"Backend: {selector.platform.best_backend}")
    print(f"GPU: {selector.platform.gpu_info}")
    
    print(f"\nDefault Model: {selector.get_default_model()}")
    
    print("\nAvailable Models:")
    for name, info in selector.get_available_models().items():
        recommended = " (RECOMMENDED)" if info.get("recommended") else ""
        print(f"  - {name}: {info['description']}{recommended}")
    
    print("\nTop 3 Recommendations:")
    for i, model in enumerate(selector.get_recommended_models(3), 1):
        print(f"  {i}. {model}")
    
    print("\nPlatform Summary:")
    import json
    print(json.dumps(selector.get_platform_summary(), indent=2, ensure_ascii=False))
