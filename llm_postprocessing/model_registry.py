"""
LLM模型注册表
包含所有候选模型的配置信息

注意: Qwen3.5 需要 mlx-lm 0.31+ 版本支持 (当前 0.31.2)
实际可用的 mlx-community 量化版本:
- mlx-community/Qwen3.5-4B-OptiQ-4bit (实测可用)
- mlx-community/Qwen3.5-2B-OptiQ-4bit (实测可用)
- mlx-community/Qwen3.5-4B-MLX-4bit (实测可用)
- mlx-community/Qwen3.5-4B-OptiQ-4bit (实测可用)
- mlx-community/Qwen3-0.6B-4bit
- mlx-community/Qwen3-1.7B-4bit
"""

from dataclasses import dataclass
from typing import List, Optional

# 默认LLM模型 (MLX 量化版本，Apple Silicon 推荐)
DEFAULT_LLM_MODEL = "Qwen3.5-4B-OptiQ"


@dataclass
class ModelInfo:
    """模型信息"""
    model_id: str              # HuggingFace模型ID (mlx-community格式)
    name: str                  # 显示名称
    size: str                  # 参数量 (e.g., "0.8B")
    is_quantized: bool         # 是否已量化
    memory_fp16: str           # FP16内存占用
    memory_int4: str           # INT4内存占用
    chinese_capability: int    # 中文能力 1-5
    speed: int                # 速度 1-5 (5最快)
    release_date: str          # 发布日期
    provider: str              # 提供商 (Qwen/Google)
    repo_type: str = "mlx"     # 仓库类型
    
    @property
    def memory_estimate(self) -> str:
        """估算内存使用（量化版本）"""
        return self.memory_int4
    
    @property
    def param_count(self) -> str:
        """参数量"""
        return self.size


# Qwen3.5系列 (2026-02/03发布, 中文最强, 需要 mlx-lm 0.31+)
# 使用 mlx-community 量化版本，实际测试可用
QWEN35_MODELS = [
    ModelInfo(
        model_id="mlx-community/Qwen3.5-4B-OptiQ-4bit",
        name="Qwen3.5-4B-OptiQ",
        size="0.8B",
        is_quantized=True,
        memory_fp16="~1.6GB",
        memory_int4="~0.8GB",
        chinese_capability=5,
        speed=5,
        release_date="2026-03-02",
        provider="Alibaba/Qwen",
        repo_type="mlx",
    ),
    ModelInfo(
        model_id="mlx-community/Qwen3.5-2B-OptiQ-4bit",
        name="Qwen3.5-2B-OptiQ",
        size="2B",
        is_quantized=True,
        memory_fp16="~4GB",
        memory_int4="~2GB",
        chinese_capability=5,
        speed=4,
        release_date="2026-03-02",
        provider="Alibaba/Qwen",
        repo_type="mlx",
    ),
    ModelInfo(
        model_id="mlx-community/Qwen3.5-4B-MLX-4bit",
        name="Qwen3.5-4B-MLX",
        size="4B",
        is_quantized=True,
        memory_fp16="~8GB",
        memory_int4="~4GB",
        chinese_capability=5,
        speed=3,
        release_date="2026-03-02",
        provider="Alibaba/Qwen",
        repo_type="mlx",
    ),
    ModelInfo(
        model_id="mlx-community/Qwen3.5-4B-OptiQ-4bit",
        name="Qwen3.5-4B-OptiQ",
        size="4B",
        is_quantized=True,
        memory_fp16="~6GB",
        memory_int4="~3GB",
        chinese_capability=5,
        speed=3,
        release_date="2026-03-02",
        provider="Alibaba/Qwen",
        repo_type="mlx",
    ),
]

# Qwen3系列 (2025-04发布, 稍旧但成熟, mlx-community版本)
QWEN3_MODELS = [
    ModelInfo(
        model_id="mlx-community/Qwen3-0.6B-4bit",
        name="Qwen3-0.6B",
        size="0.6B",
        is_quantized=True,
        memory_fp16="~1.2GB",
        memory_int4="~0.5GB",
        chinese_capability=4,
        speed=5,
        release_date="2025-04-10",
        provider="Alibaba/Qwen",
        repo_type="mlx",
    ),
    ModelInfo(
        model_id="mlx-community/Qwen3-1.7B-4bit",
        name="Qwen3-1.7B",
        size="1.7B",
        is_quantized=True,
        memory_fp16="~3.4GB",
        memory_int4="~1.5GB",
        chinese_capability=4,
        speed=4,
        release_date="2025-04-10",
        provider="Alibaba/Qwen",
        repo_type="mlx",
    ),
]

# Gemma 4系列 (2026-03-31发布, 最新但中文较弱)
# ⚠️ 注意: Gemma 4 需要 HuggingFace 认证 (gated repo)
Gemma4_MODELS = [
    ModelInfo(
        model_id="google/gemma-4-2b-it",
        name="Gemma 4-2B",
        size="2B",
        is_quantized=True,
        memory_fp16="~4GB",
        memory_int4="~2GB",
        chinese_capability=3,
        speed=4,
        release_date="2026-03-31",
        provider="Google DeepMind",
        repo_type="mlx",
    ),
    ModelInfo(
        model_id="google/gemma-4-4b-it",
        name="Gemma 4-4B",
        size="4B",
        is_quantized=True,
        memory_fp16="~8GB",
        memory_int4="~4GB",
        chinese_capability=3,
        speed=3,
        release_date="2026-03-31",
        provider="Google DeepMind",
        repo_type="mlx",
    ),
]

# 所有候选模型 (按推荐顺序)
ALL_MODELS = QWEN35_MODELS + QWEN3_MODELS + Gemma4_MODELS


def get_model_by_name(name: str) -> Optional[ModelInfo]:
    """根据名称获取模型信息"""
    for model in ALL_MODELS:
        if model.name == name:
            return model
    return None


def get_model_by_id(model_id: str) -> Optional[ModelInfo]:
    """根据模型ID获取模型信息"""
    for model in ALL_MODELS:
        if model.model_id == model_id:
            return model
    return None


def get_models_by_provider(provider: str) -> List[ModelInfo]:
    """根据提供商获取模型列表"""
    return [m for m in ALL_MODELS if m.provider == provider]


def get_models_by_size(size_limit: str = "4B") -> List[ModelInfo]:
    """获取小于等于指定尺寸的模型"""
    size_order = {"0.8B": 0.8, "0.6B": 0.6, "1.7B": 1.7, "2B": 2, "4B": 4, "9B": 9}
    limit_val = size_order.get(size_limit, 4)
    return [m for m in ALL_MODELS if size_order.get(m.size, 99) <= limit_val]


def get_all_model_names() -> List[str]:
    """获取所有模型名称"""
    return [m.name for m in ALL_MODELS]


def get_default_model() -> ModelInfo:
    """获取默认模型"""
    return get_model_by_name(DEFAULT_LLM_MODEL) or ALL_MODELS[0]


def print_model_table():
    """打印模型对比表"""
    print("\n" + "=" * 110)
    print(f"{'模型':<18} {'参数量':<8} {'内存(INT4)':<10} {'中文':<6} {'速度':<6} {'发布日期':<12} {'提供商'}")
    print("-" * 110)
    for m in ALL_MODELS:
        chinese_stars = "★" * m.chinese_capability + "☆" * (5 - m.chinese_capability)
        speed_bars = "⚡" * m.speed
        print(f"{m.name:<18} {m.size:<8} {m.memory_int4:<10} {chinese_stars:<6} {speed_bars:<6} {m.release_date:<12} {m.provider}")
    print("=" * 110)
    print(f"\n默认模型: {DEFAULT_LLM_MODEL}")


if __name__ == "__main__":
    print_model_table()
