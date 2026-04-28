#!/usr/bin/env python3
"""
Voice Input Framework - 共享模型注册表

所有 STT 模型的统一配置和注册。
这是模型元数据 (MODELS_CONFIG) + 引擎类映射 (AVAILABLE_MODELS) 的单一来源。
"""

import platform
from typing import Dict, Any

# Apple Silicon 检测
IS_APPLE_SILICON = platform.machine() == "arm64" and platform.system() == "Darwin"

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
    },
    "qwen_asr_mlx_native_small": {
        "model_id": "mlx-community/Qwen3-ASR-0.6B-4bit",
        "engine": "qwen_asr_mlx_native",
        "aligner_id": None,
        "memory_gb": 0.5,
        "description": "Qwen3-ASR-0.6B MLX 4bit (MLX原生，更快)",
        "requires_apple_silicon": True,
    },
    # ── MLX Whisper 模型 (Apple Silicon) ──
    "whisper_mlx": {
        "model_id": "mlx-community/whisper-large-v3-mlx",
        "engine": "whisper_mlx",
        "aligner_id": None,
        "memory_gb": 3.0,
        "description": "MLX Whisper Large V3 (Apple Silicon)",
        "requires_apple_silicon": True,
    },
    "whisper_mlx_turbo": {
        "model_id": "mlx-community/whisper-large-v3-turbo-mlx",
        "engine": "whisper_mlx",
        "aligner_id": None,
        "memory_gb": 2.0,
        "description": "MLX Whisper Large V3 Turbo (快速+准确，Apple Silicon)",
        "requires_apple_silicon": True,
    },
    "whisper_mlx_medium": {
        "model_id": "mlx-community/whisper-medium-mlx",
        "engine": "whisper_mlx",
        "aligner_id": None,
        "memory_gb": 1.5,
        "description": "MLX Whisper Medium (Apple Silicon)",
        "requires_apple_silicon": True,
    },
    "whisper_mlx_small": {
        "model_id": "mlx-community/whisper-small-mlx",
        "engine": "whisper_mlx",
        "aligner_id": None,
        "memory_gb": 0.5,
        "description": "MLX Whisper Small (最快，Apple Silicon)",
        "requires_apple_silicon": True,
    },
    # ── Whisper.cpp 模型 (C++ 实现) ──
    "whisper_cpp_base": {
        "model_id": "whisper_cpp_base",
        "engine": "whisper_cpp",
        "whisper_model": "whisper-v3-base",
        "aligner_id": None,
        "memory_gb": 1,
        "description": "Whisper V3 Base via whisper.cpp (Metal GPU, fast)",
    },
    "whisper_cpp_large": {
        "model_id": "whisper_cpp_large",
        "engine": "whisper_cpp",
        "whisper_model": "whisper-v3-large",
        "aligner_id": None,
        "memory_gb": 3,
        "description": "Whisper V3 Large via whisper.cpp (Metal GPU, accurate)",
    },
    # ── Whisper Transformers 模型 (通用备选) ──
    "whisper_turbo": {
        "model_id": "openai/whisper-large-v3-turbo",
        "engine": "whisper_turbo",
        "aligner_id": None,
        "memory_gb": 3,
        "description": "Whisper Large V3 Turbo (transformers, fast)",
    },
}


def get_default_model() -> str:
    """返回当前平台推荐的默认模型"""
    return "qwen_asr_mlx_native_small" if IS_APPLE_SILICON else "qwen_asr"


def get_apple_silicon_only_models() -> list:
    """返回需要 Apple Silicon 的模型列表"""
    return [name for name, cfg in MODELS_CONFIG.items() if cfg.get("requires_apple_silicon")]
