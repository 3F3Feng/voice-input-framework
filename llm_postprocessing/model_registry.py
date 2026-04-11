"""
LLM模型注册表
包含所有候选模型的配置信息
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ModelInfo:
    """模型信息"""
    model_id: str              # HuggingFace模型ID
    name: str                  # 显示名称
    size: str                  # 参数量 (e.g., "0.8B")
    is_quantized: bool         # 是否已量化
    memory_fp16: str           # FP16内存占用
    memory_int4: str           # INT4内存占用
    chinese_capability: int     # 中文能力 1-5
    speed: int                 # 速度 1-5 (5最快)
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


# Qwen3.5系列 (2026-02/03发布, 中文最强)
# ⚠️ 注意: Qwen3.5 需要更新版 mlx-lm (当前 0.29.1 不支持 qwen3_5 类型)
QWEN35_MODELS = [
    ModelInfo(
        model_id="Qwen/Qwen3.5-0.8B",
        name="Qwen3.5-0.8B",
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
        model_id="Qwen/Qwen3.5-2B",
        name="Qwen3.5-2B",
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
        model_id="Qwen/Qwen3.5-4B",
        name="Qwen3.5-4B",
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
        model_id="Qwen/Qwen3.5-9B",
        name="Qwen3.5-9B",
        size="9B",
        is_quantized=True,
        memory_fp16="~18GB",
        memory_int4="~9GB",
        chinese_capability=5,
        speed=2,
        release_date="2026-03-02",
        provider="Alibaba/Qwen",
        repo_type="mlx",
    ),
]

# Qwen3系列 (2025-04发布, 稍旧但成熟)
QWEN3_MODELS = [
    ModelInfo(
        model_id="Qwen/Qwen3-0.6B",
        name="Qwen3-0.6B",
        size="0.6B",
        is_quantized=True,
        memory_fp16="~1.2GB",
        memory_int4="~0.5GB",
        chinese_capability=4,
        speed=5,
        release_date="2025-04-10",
        provider="Alibaba/Qwen",
    ),
    ModelInfo(
        model_id="Qwen/Qwen3-1.7B",
        name="Qwen3-1.7B",
        size="1.7B",
        is_quantized=True,
        memory_fp16="~3.4GB",
        memory_int4="~1.5GB",
        chinese_capability=4,
        speed=4,
        release_date="2025-04-10",
        provider="Alibaba/Qwen",
    ),
]

# Gemma 4系列 (2026-03-31发布, 最新但中文较弱)
# ⚠️ 注意: Gemma 4 需要 HuggingFace 认证 (gated repo)
Gemma4_MODELS = [
    ModelInfo(
        model_id="google/gemma-4-2b-it",
        name="Gemma 4-E2B",
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
        name="Gemma 4-E4B",
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

# 所有候选模型
ALL_MODELS = QWEN35_MODELS + QWEN3_MODELS + Gemma4_MODELS


def get_model_by_name(name: str) -> Optional[ModelInfo]:
    """根据名称获取模型信息"""
    for model in ALL_MODELS:
        if model.name == name:
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


def print_model_table():
    """打印模型对比表"""
    print("\n" + "=" * 100)
    print(f"{'模型':<15} {'参数量':<8} {'内存(INT4)':<10} {'中文':<6} {'速度':<6} {'发布日期':<12} {'提供商'}")
    print("-" * 100)
    for m in ALL_MODELS:
        print(f"{m.name:<15} {m.size:<8} {m.memory_int4:<10} {m.chinese_capability*'★':<5} {m.speed*'⚡':<5} {m.release_date:<12} {m.provider}")
    print("=" * 100)


if __name__ == "__main__":
    print_model_table()
