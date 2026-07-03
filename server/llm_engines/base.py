#!/usr/bin/env python3
"""
Voice Input Framework - LLM 引擎基类

定义所有 LLM 引擎必须实现的接口。
支持 MLX (Apple Silicon) 和 CUDA (NVIDIA) 后端。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    """LLM 处理结果"""

    text: str  # 处理后的文本
    original_text: str  # 原始文本
    latency_ms: float  # 处理延迟 (ms)
    model: str  # 使用的模型名称
    success: bool = True  # 是否成功
    error: Optional[str] = None  # 错误信息


class BaseLLMEngine(ABC):
    """
    LLM 引擎抽象基类

    所有 LLM 模型实现必须继承此类并实现其方法。
    """

    def __init__(self, model_name: str, model_id: str, **kwargs):
        """
        初始化 LLM 引擎

        Args:
            model_name: 模型显示名称 (如 "Qwen3.5-4B-OptiQ")
            model_id: HuggingFace 模型 ID (如 "mlx-community/Qwen3.5-4B-OptiQ-4bit")
        """
        self.model_name = model_name
        self.model_id = model_id
        self._model = None
        self._tokenizer = None
        self._is_loaded = False
        self._is_loading = False

    @property
    def is_loaded(self) -> bool:
        """检查模型是否已加载"""
        return self._is_loaded

    @property
    def is_loading(self) -> bool:
        """检查模型是否正在加载"""
        return self._is_loading

    @abstractmethod
    async def load(self) -> bool:
        """
        加载模型

        Returns:
            是否加载成功
        """
        pass

    @abstractmethod
    async def unload(self) -> None:
        """卸载模型，释放资源"""
        pass

    @abstractmethod
    async def generate(self, prompt: str, max_tokens: int = 256) -> str:
        """
        生成文本

        Args:
            prompt: 输入提示
            max_tokens: 最大生成 token 数

        Returns:
            生成的文本
        """
        pass

    async def process(self, text: str, system_prompt: str = "", max_tokens: int = 256) -> LLMResult:
        """
        处理文本（带提示词）

        Args:
            text: 输入文本
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数

        Returns:
            LLMResult 对象
        """
        import time

        start_time = time.time()

        try:
            if not self._is_loaded:
                loaded = await self.load()
                if not loaded:
                    return LLMResult(
                        text=text,
                        original_text=text,
                        latency_ms=0,
                        model=self.model_name,
                        success=False,
                        error="Failed to load model",
                    )

            # 构建完整提示
            # 注意: CUDA/MFX 引擎的 _generate_sync 内部使用 tokenizer.apply_chat_template，
            # 不需要手动添加 chat format prefix。传入纯文本作为 user message 即可。
            if system_prompt:
                full_prompt = f"{system_prompt}\n\n{text}"
            else:
                full_prompt = text

            # 生成
            generated = await self.generate(full_prompt, max_tokens)

            # 清理响应
            cleaned = self._clean_response(generated)

            latency_ms = (time.time() - start_time) * 1000

            return LLMResult(
                text=cleaned,
                original_text=text,
                latency_ms=latency_ms,
                model=self.model_name,
                success=True,
            )

        except Exception as e:
            logger.error(f"LLM processing error: {e}")
            latency_ms = (time.time() - start_time) * 1000
            return LLMResult(
                text=text,
                original_text=text,
                latency_ms=latency_ms,
                model=self.model_name,
                success=False,
                error=str(e),
            )

    def _clean_response(self, text: str) -> str:
        """
        清理 LLM 响应

        Args:
            text: 原始响应

        Returns:
            清理后的文本
        """
        import re

        # 移除思考标签
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text)
        cleaned = re.sub(r"</?think>", "", cleaned)

        # 移除 markdown 标记
        cleaned = cleaned.replace("**", "")
        cleaned = cleaned.replace('"', "")
        cleaned = cleaned.replace("'", "")

        # 移除重复行
        lines = cleaned.split("\n")
        unique_lines = []
        for line in lines:
            line = line.strip()
            if line and line not in unique_lines:
                unique_lines.append(line)

        cleaned = " ".join(unique_lines)
        cleaned = cleaned.strip()

        return cleaned

    def get_info(self) -> dict:
        """获取引擎信息"""
        return {
            "model_name": self.model_name,
            "model_id": self.model_id,
            "is_loaded": self._is_loaded,
            "is_loading": self._is_loading,
            "backend": self.__class__.__name__,
        }


class LLMEngineError(Exception):
    """LLM 引擎错误"""

    pass
