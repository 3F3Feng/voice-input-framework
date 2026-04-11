"""
LLM后处理模块
用于评估和选择适合语音输入后处理的LLM模型
"""

from llm_postprocessing.model_registry import (
    ALL_MODELS,
    QWEN35_MODELS,
    QWEN3_MODELS,
    Gemma4_MODELS,
    get_model_by_name,
    get_models_by_size,
    get_all_model_names,
    print_model_table,
)

from llm_postprocessing.test_dataset import (
    get_test_cases,
    get_perf_test_cases,
    get_categories,
)

from llm_postprocessing.evaluator import (
    LLMEvaluator,
    ModelBenchmark,
    TestResult,
    run_all_benchmarks,
    print_comparison_table,
    save_results,
)

__all__ = [
    # Model registry
    "ALL_MODELS",
    "QWEN35_MODELS",
    "QWEN3_MODELS",
    "Gemma4_MODELS",
    "get_model_by_name",
    "get_models_by_size",
    "get_all_model_names",
    "print_model_table",
    # Test dataset
    "get_test_cases",
    "get_perf_test_cases",
    "get_categories",
    # Evaluator
    "LLMEvaluator",
    "ModelBenchmark",
    "TestResult",
    "run_all_benchmarks",
    "print_comparison_table",
    "save_results",
]
