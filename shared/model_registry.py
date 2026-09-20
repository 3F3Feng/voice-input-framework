#!/usr/bin/env python3
"""
Voice Input Framework - 共享模型注册表

所有 STT 模型的统一配置和注册。
这是模型元数据 (MODELS_CONFIG) + 引擎类映射 (AVAILABLE_MODELS) 的单一来源。
"""

import platform
from typing import Any

# Apple Silicon 检测
IS_APPLE_SILICON = platform.machine() == "arm64" and platform.system() == "Darwin"

# 模型配置：名称 → 元数据
# engine_type 必须与 services/stt_server.py 和 server/models/base.py 中的加载代码匹配
MODELS_CONFIG: dict[str, dict[str, Any]] = {
    # ── MLX 原生模型 (mlx-audio，Apple Silicon 优化，推荐) ──
    "qwen_asr_mlx_native": {
        "model_id": "mlx-community/Qwen3-ASR-1.7B-8bit",
        "engine": "qwen_asr_mlx_native",
        "memory_gb": 1.0,
        "description": "Qwen3-ASR-1.7B MLX 8bit (MLX原生，推荐)",
        "requires_apple_silicon": True,
    },
    "qwen_asr_mlx_native_small": {
        "model_id": "mlx-community/Qwen3-ASR-0.6B-4bit",
        "engine": "qwen_asr_mlx_native",
        "memory_gb": 0.5,
        "description": "Qwen3-ASR-0.6B MLX 4bit (MLX原生，更快)",
        "requires_apple_silicon": True,
    },
    # ── MLX Whisper 模型 (Apple Silicon) ──
    "whisper_mlx": {
        "model_id": "mlx-community/whisper-large-v3-mlx",
        "engine": "whisper_mlx",
        "memory_gb": 3.0,
        "description": "MLX Whisper Large V3 (Apple Silicon)",
        "requires_apple_silicon": True,
    },
    "whisper_mlx_turbo": {
        "model_id": "mlx-community/whisper-large-v3-turbo-mlx",
        "engine": "whisper_mlx",
        "memory_gb": 2.0,
        "description": "MLX Whisper Large V3 Turbo (快速+准确，Apple Silicon)",
        "requires_apple_silicon": True,
    },
    "whisper_mlx_medium": {
        "model_id": "mlx-community/whisper-medium-mlx",
        "engine": "whisper_mlx",
        "memory_gb": 1.5,
        "description": "MLX Whisper Medium (Apple Silicon)",
        "requires_apple_silicon": True,
    },
    "whisper_mlx_small": {
        "model_id": "mlx-community/whisper-small-mlx",
        "engine": "whisper_mlx",
        "memory_gb": 0.5,
        "description": "MLX Whisper Small (最快，Apple Silicon)",
        "requires_apple_silicon": True,
    },
    # ── Whisper.cpp 模型 (C++ 实现) ──
    "whisper_cpp_base": {
        "model_id": "whisper_cpp_base",
        "engine": "whisper_cpp",
        "whisper_model": "whisper-v3-base",
        "memory_gb": 1,
        "description": "Whisper V3 Base via whisper.cpp (Metal GPU, fast)",
    },
    "whisper_cpp_large": {
        "model_id": "whisper_cpp_large",
        "engine": "whisper_cpp",
        "whisper_model": "whisper-v3-large",
        "memory_gb": 3,
        "description": "Whisper V3 Large via whisper.cpp (Metal GPU, accurate)",
    },
    # ── Whisper Transformers 模型(跨平台:Windows / Linux / macOS 都能跑)──
    #
    # 非 Apple 平台以前**只有 whisper_turbo 一个选择**,而它是个 1.6GB 的大模型
    # —— 在没有独显的机器上又慢又占内存,想换小一点的却没得换。这里按大小补齐一
    # 条梯度,让用户能按自己的硬件挑。
    #
    # memory_gb 是 fp16 权重加推理开销的粗估;CPU 上跑 fp32 大约要乘 2。
    "whisper_tiny": {
        "model_id": "openai/whisper-tiny",
        "engine": "whisper_turbo",
        "memory_gb": 0.3,
        "description": "Whisper Tiny (transformers, 最快, 精度一般, 适合低配 / 纯 CPU)",
    },
    "whisper_base": {
        "model_id": "openai/whisper-base",
        "engine": "whisper_turbo",
        "memory_gb": 0.5,
        "description": "Whisper Base (transformers, 纯 CPU 上的推荐起点)",
    },
    "whisper_small": {
        "model_id": "openai/whisper-small",
        "engine": "whisper_turbo",
        "memory_gb": 1.0,
        "description": "Whisper Small (transformers, 速度与精度折中)",
    },
    "whisper_medium": {
        "model_id": "openai/whisper-medium",
        "engine": "whisper_turbo",
        "memory_gb": 2.5,
        "description": "Whisper Medium (transformers, 有独显时适用)",
    },
    "whisper_turbo": {
        "model_id": "openai/whisper-large-v3-turbo",
        "engine": "whisper_turbo",
        "memory_gb": 3,
        "description": "Whisper Large V3 Turbo (transformers, 精度最好, 建议配 GPU)",
    },
}


def get_default_model() -> str:
    """返回当前平台推荐的默认模型。

    这是**不看硬件的静态兜底**,取的是「哪台机器上都跑得动」的那一档。
    真正的选型在 `services.device.recommend_stt_model()`:那里会量核数、
    内存和显存,配得上更大的模型就自动升上去。这里之所以还要保守,是因为
    这个函数是 `STTEngine.__init__` 的默认参数,在模块导入时就会求值 ——
    不能在这里 import torch。

    非 Apple 的兜底不用 `whisper_turbo`(1.6GB):实测在 6 核 i5 上
    large 一档远慢于实时,默认给个跑不动的只会让人以为程序坏了。

    可用 `VIF_STT_MODEL` 覆盖。
    """
    return "qwen_asr_mlx_native_small" if IS_APPLE_SILICON else "whisper_base"


def get_apple_silicon_only_models() -> list:
    """返回需要 Apple Silicon 的模型列表"""
    return [name for name, cfg in MODELS_CONFIG.items() if cfg.get("requires_apple_silicon")]
