#!/usr/bin/env python3
"""
Voice Input Framework - 共享模型注册表

所有 STT 模型的统一配置和注册。
这是模型元数据 (MODELS_CONFIG) + 引擎类映射 (AVAILABLE_MODELS) 的单一来源。
"""

from typing import Dict, Any

# 使用统一平台检测模块
from shared.platform_detector import detect_platform, get_platform_info

# 向后兼容：保留 IS_APPLE_SILICON，但使用统一检测
_platform = detect_platform()
IS_APPLE_SILICON = _platform.is_apple_silicon

# 模型配置：名称 → 元数据
# engine_type 必须与 services/stt_server.py 和 server/models/base.py 中的加载代码匹配
MODELS_CONFIG: Dict[str, Dict[str, Any]] = {
    # ── MLX 原生模型 (mlx-audio，Apple Silicon 优化，推荐) ──
    "qwen_asr_mlx_native": {
        "model_id": "mlx-community/Qwen3-ASR-1.7B-8bit",
        "engine": "qwen_asr_mlx_native",
        "aligner_id": None,
        "memory_gb": 1.0,
        "description": "Qwen3-ASR-1.7B MLX 8bit (MLX原生，推荐)",
        "requires_apple_silicon": True,
        "requires_mlx": True,
    },
    "qwen_asr_mlx_native_small": {
        "model_id": "mlx-community/Qwen3-ASR-0.6B-4bit",
        "engine": "qwen_asr_mlx_native",
        "aligner_id": None,
        "memory_gb": 0.5,
        "description": "Qwen3-ASR-0.6B MLX 4bit (MLX原生，更快)",
        "requires_apple_silicon": True,
        "requires_mlx": True,
    },
    # ── MLX Whisper 模型 (Apple Silicon) ──
    "whisper_mlx": {
        "model_id": "mlx-community/whisper-large-v3-mlx",
        "engine": "whisper_mlx",
        "aligner_id": None,
        "memory_gb": 3.0,
        "description": "MLX Whisper Large V3 (Apple Silicon)",
        "requires_apple_silicon": True,
        "requires_mlx": True,
    },
    "whisper_mlx_turbo": {
        "model_id": "mlx-community/whisper-large-v3-turbo-mlx",
        "engine": "whisper_mlx",
        "aligner_id": None,
        "memory_gb": 2.0,
        "description": "MLX Whisper Large V3 Turbo (快速+准确，Apple Silicon)",
        "requires_apple_silicon": True,
        "requires_mlx": True,
    },
    "whisper_mlx_medium": {
        "model_id": "mlx-community/whisper-medium-mlx",
        "engine": "whisper_mlx",
        "aligner_id": None,
        "memory_gb": 1.5,
        "description": "MLX Whisper Medium (Apple Silicon)",
        "requires_apple_silicon": True,
        "requires_mlx": True,
    },
    "whisper_mlx_small": {
        "model_id": "mlx-community/whisper-small-mlx",
        "engine": "whisper_mlx",
        "aligner_id": None,
        "memory_gb": 0.5,
        "description": "MLX Whisper Small (最快，Apple Silicon)",
        "requires_apple_silicon": True,
        "requires_mlx": True,
    },
    # ── Whisper.cpp 模型 (C++ 实现) ──
    "whisper_cpp_base": {
        "model_id": "whisper_cpp_base",
        "engine": "whisper_cpp",
        "whisper_model": "whisper-v3-base",
        "aligner_id": None,
        "memory_gb": 1,
        "description": "Whisper V3 Base via whisper.cpp (Metal GPU, fast)",
        "requires_macos": True,
    },
    "whisper_cpp_large": {
        "model_id": "whisper_cpp_large",
        "engine": "whisper_cpp",
        "whisper_model": "whisper-v3-large",
        "aligner_id": None,
        "memory_gb": 3,
        "description": "Whisper V3 Large via whisper.cpp (Metal GPU, accurate)",
        "requires_macos": True,
    },
    # ── CUDA 模型 (NVIDIA GPU) ──
    "qwen_asr_cuda": {
        "model_id": "Qwen/Qwen3-ASR-1.7B",
        "engine": "qwen_asr_cuda",
        "aligner_id": None,
        "memory_gb": 3.5,
        "description": "Qwen3-ASR-1.7B CUDA FP16 (NVIDIA GPU，推荐)",
        "requires_cuda": True,
    },
    "qwen_asr_cuda_small": {
        "model_id": "Qwen/Qwen3-ASR-0.6B",
        "engine": "qwen_asr_cuda",
        "aligner_id": None,
        "memory_gb": 1.5,
        "description": "Qwen3-ASR-0.6B CUDA FP16 (更快，NVIDIA GPU)",
        "requires_cuda": True,
    },
    "qwen_asr_cuda_int8": {
        "model_id": "Qwen/Qwen3-ASR-1.7B",
        "engine": "qwen_asr_cuda",
        "aligner_id": None,
        "memory_gb": 2.0,
        "description": "Qwen3-ASR-1.7B CUDA int8 (省内存，NVIDIA GPU)",
        "requires_cuda": True,
    },
    "qwen_asr_cuda_small_int8": {
        "model_id": "Qwen/Qwen3-ASR-0.6B",
        "engine": "qwen_asr_cuda",
        "aligner_id": None,
        "memory_gb": 1.0,
        "description": "Qwen3-ASR-0.6B CUDA int8 (最快，NVIDIA GPU)",
        "requires_cuda": True,
    },
    # ── Whisper Transformers 模型 (通用备选) ──
    "whisper_turbo": {
        "model_id": "openai/whisper-large-v3-turbo",
        "engine": "whisper_turbo",
        "aligner_id": None,
        "memory_gb": 3,
        "description": "Whisper Large V3 Turbo (transformers, fast)",
        "requires_cuda": False,  # 通用，不需要 CUDA
    },
}


def get_default_model() -> str:
    """返回当前平台推荐的默认模型
    
    优先级:
    1. Apple Silicon + MLX -> qwen_asr_mlx_native_small
    2. NVIDIA CUDA GPU -> qwen_asr_cuda (显存>=8GB) 或 qwen_asr_cuda_small
    3. 其他 -> whisper_turbo (通用 CPU/MPS)
    """
    platform_info = get_platform_info()
    
    # Apple Silicon: 优先 MLX
    if platform_info.is_apple_silicon and platform_info.has_mlx:
        return "qwen_asr_mlx_native_small"
    
    # NVIDIA GPU: 优先 CUDA
    if platform_info.has_cuda:
        if platform_info.cuda_device and platform_info.cuda_device.memory_gb >= 8:
            return "qwen_asr_cuda"
        else:
            return "qwen_asr_cuda_small"
    
    # 其他平台: 通用模型
    return "whisper_turbo"


def get_available_models() -> Dict[str, Dict[str, Any]]:
    """返回当前平台可用的模型（过滤掉不支持的模型）"""
    platform_info = get_platform_info()
    available = {}
    
    for name, config in MODELS_CONFIG.items():
        # 检查平台要求
        if config.get("requires_apple_silicon") and not platform_info.is_apple_silicon:
            continue
        if config.get("requires_mlx") and not platform_info.has_mlx:
            continue
        if config.get("requires_cuda") and not platform_info.has_cuda:
            continue
        if config.get("requires_macos") and not platform_info.is_macos:
            continue
        
        available[name] = config
    
    return available


def get_apple_silicon_only_models() -> list:
    """返回需要 Apple Silicon 的模型列表"""
    return [name for name, cfg in MODELS_CONFIG.items() if cfg.get("requires_apple_silicon")]


def get_cuda_only_models() -> list:
    """返回需要 CUDA 的模型列表"""
    return [name for name, cfg in MODELS_CONFIG.items() if cfg.get("requires_cuda")]


# ============== LLM 模型注册表 ==============

# LLM 模型配置：名称 → 元数据
# backend 必须与 server/llm_engines/ 中的引擎类对应
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


def get_default_llm_model() -> str:
    """返回当前平台推荐的默认 LLM 模型
    
    优先级:
    1. Apple Silicon + MLX -> Qwen3.5-4B-OptiQ
    2. NVIDIA CUDA GPU -> Qwen3.5-4B-CUDA (显存>=8GB) 或 Qwen3.5-2B-CUDA
    3. 其他 -> "" (不推荐在 CPU 上运行 LLM)
    """
    platform_info = get_platform_info()
    
    # Apple Silicon: 优先 MLX
    if platform_info.is_apple_silicon and platform_info.has_mlx:
        return "Qwen3.5-4B-OptiQ"
    
    # NVIDIA GPU: 优先 CUDA
    if platform_info.has_cuda:
        if platform_info.cuda_device and platform_info.cuda_device.memory_gb >= 8:
            return "Qwen3.5-4B-CUDA"
        else:
            return "Qwen3.5-2B-CUDA"
    
    # 其他平台: 不推荐运行 LLM
    return ""


def get_available_llm_models() -> Dict[str, Dict[str, Any]]:
    """返回当前平台可用的 LLM 模型（过滤掉不支持的模型）"""
    platform_info = get_platform_info()
    available = {}
    
    for name, config in LLM_MODELS_CONFIG.items():
        # 检查平台要求
        if config.get("requires_mlx") and not platform_info.has_mlx:
            continue
        if config.get("requires_cuda") and not platform_info.has_cuda:
            continue
        
        # 检查 VRAM 要求
        if config.get("requires_cuda") and platform_info.cuda_device:
            if config["memory_gb"] > platform_info.cuda_device.memory_gb:
                continue
        
        available[name] = config
    
    return available
