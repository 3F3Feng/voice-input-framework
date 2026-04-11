"""
Voice Input Framework - LLM 后处理引擎

管理 LLM 模型的加载和处理。
"""

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class LLMEngine:
    """LLM 后处理引擎"""

    def __init__(self, config=None):
        """
        初始化 LLM 引擎

        Args:
            config: LLMConfig 对象
        """
        self.config = config
        self._evaluator = None
        self._current_model_name: str = ""
        self._is_processing = False

    def load_model(self, model_name: str) -> bool:
        """
        加载指定的 LLM 模型

        Args:
            model_name: 模型名称 (如 "Qwen3.5-0.8B-OptiQ")

        Returns:
            是否加载成功
        """
        try:
            from llm_postprocessing.model_registry import get_model_by_name

            model_info = get_model_by_name(model_name)
            if not model_info:
                logger.error(f"Unknown LLM model: {model_name}")
                return False

            # 如果已经加载了同样的模型，直接返回
            if self._current_model_name == model_name and self._evaluator is not None:
                logger.info(f"LLM model {model_name} already loaded")
                return True

            # 卸载旧模型
            if self._evaluator:
                self._evaluator.unload()
                self._evaluator = None

            # 加载新模型
            logger.info(f"Loading LLM model: {model_name} ({model_info.model_id})")
            from llm_postprocessing.evaluator import LLMEvaluator

            thinking_timeout = self.config.thinking_timeout if self.config else 5.0
            self._evaluator = LLMEvaluator(
                model_info.model_id,
                verbose=False,
                thinking_timeout=thinking_timeout
            )

            if not self._evaluator.load():
                logger.error(f"Failed to load LLM model: {model_name}")
                self._evaluator = None
                return False

            self._current_model_name = model_name
            logger.info(f"LLM model loaded successfully: {model_name}")
            return True

        except Exception as e:
            logger.error(f"Error loading LLM model {model_name}: {e}")
            self._evaluator = None
            return False

    def unload(self):
        """卸载当前模型"""
        if self._evaluator:
            self._evaluator.unload()
            self._evaluator = None
            self._current_model_name = ""
            logger.info("LLM model unloaded")

    def process(self, text: str) -> tuple[str, float]:
        """
        处理文本

        Args:
            text: 输入文本

        Returns:
            (处理结果, 延迟ms)
        """
        if not self._evaluator:
            logger.warning("No LLM model loaded, returning original text")
            return text, 0.0

        try:
            self._is_processing = True
            start_time = time.time()

            result, latency = self._evaluator.process(text)

            elapsed = (time.time() - start_time) * 1000
            logger.info(f"LLM processing: input={text[:30]}..., output={result[:30]}..., llm_latency={latency:.0f}ms, total_latency={elapsed:.0f}ms")

            return result, latency

        except Exception as e:
            logger.error(f"Error processing text: {e}")
            return text, -1
        finally:
            self._is_processing = False

    @property
    def current_model(self) -> str:
        """获取当前模型名称"""
        return self._current_model_name

    @property
    def is_loaded(self) -> bool:
        """检查是否已加载模型"""
        return self._evaluator is not None

    @property
    def is_processing(self) -> bool:
        """检查是否正在处理"""
        return self._is_processing


class LLMManager:
    """LLM 管理器（单例模式）"""

    _instance: Optional["LLMManager"] = None

    def __init__(self, config=None):
        self.config = config
        self._engine: Optional[LLMEngine] = None
        self._available_models: list = []

    @classmethod
    def get_instance(cls, config=None) -> "LLMManager":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls(config)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化"""
        try:
            from llm_postprocessing.model_registry import get_all_model_names, get_default_model

            self._available_models = get_all_model_names()

            # 加载默认模型
            default_model = get_default_model()
            if default_model:
                self._engine = LLMEngine(self.config)
                if not self._engine.load_model(default_model.name):
                    logger.warning(f"Failed to load default LLM model: {default_model.name}")
                    self._engine = None
        except Exception as e:
            logger.error(f"Error initializing LLM manager: {e}")
            self._engine = None

    def get_engine(self) -> Optional[LLMEngine]:
        """获取引擎实例"""
        return self._engine

    def switch_model(self, model_name: str) -> bool:
        """
        切换模型

        Args:
            model_name: 模型名称

        Returns:
            是否切换成功
        """
        if not self._engine:
            self._engine = LLMEngine(self.config)

        return self._engine.load_model(model_name)

    def get_available_models(self) -> list:
        """获取可用模型列表"""
        return self._available_models

    def get_current_model(self) -> str:
        """获取当前模型名称"""
        return self._engine.current_model if self._engine else ""

    def unload(self):
        """卸载模型"""
        if self._engine:
            self._engine.unload()
