#!/usr/bin/env python3
"""
Voice Input Framework - CUDA LLM 引擎

NVIDIA GPU 优化的 LLM 引擎，使用 transformers + bitsandbytes。
支持 Flash Attention 2 和 int8/int4 量化。
"""

import asyncio
import logging
from typing import Optional

from server.llm_engines.base import BaseLLMEngine, LLMEngineError

logger = logging.getLogger(__name__)


class CUDALLMEngine(BaseLLMEngine):
    """
    CUDA LLM 引擎

    使用 transformers 库在 NVIDIA GPU 上运行 LLM 模型。
    支持:
    - Flash Attention 2 (需要 flash-attn 包)
    - int8 量化 (需要 bitsandbytes 包)
    - int4 量化 (需要 bitsandbytes 包)
    - torch.compile 优化
    """

    # 模型配置：名称 -> (HuggingFace ID, 内存需求 GB, 量化类型)
    MODEL_CONFIGS = {
        "Qwen3.5-4B-CUDA": {
            "model_id": "Qwen/Qwen3.5-4B",
            "memory_gb": 8.0,
            "dtype": "float16",
            "description": "Qwen3.5-4B CUDA FP16 (推荐，8GB VRAM)",
        },
        "Qwen3.5-2B-CUDA": {
            "model_id": "Qwen/Qwen3.5-2B",
            "memory_gb": 4.0,
            "dtype": "float16",
            "description": "Qwen3.5-2B CUDA FP16 (4GB VRAM)",
        },
        "Qwen3.5-4B-CUDA-INT8": {
            "model_id": "Qwen/Qwen3.5-4B",
            "memory_gb": 4.0,
            "dtype": "int8",
            "description": "Qwen3.5-4B CUDA int8 量化 (4GB VRAM)",
        },
        "Qwen3.5-2B-CUDA-INT8": {
            "model_id": "Qwen/Qwen3.5-2B",
            "memory_gb": 2.0,
            "dtype": "int8",
            "description": "Qwen3.5-2B CUDA int8 量化 (2GB VRAM)",
        },
        "Qwen3.5-4B-CUDA-INT4": {
            "model_id": "Qwen/Qwen3.5-4B",
            "memory_gb": 2.5,
            "dtype": "int4",
            "description": "Qwen3.5-4B CUDA int4 量化 (2.5GB VRAM)",
        },
    }

    def __init__(self, model_name: str, model_id: str, dtype: str = "float16", **kwargs):
        super().__init__(model_name, model_id, **kwargs)
        self._dtype = dtype
        self._model = None
        self._tokenizer = None
        self._device = None

    async def load(self) -> bool:
        """加载 CUDA 模型"""
        if self._is_loaded:
            return True

        if self._is_loading:
            logger.info("CUDA model is loading, waiting...")
            while self._is_loading:
                await asyncio.sleep(0.5)
            return self._is_loaded

        self._is_loading = True

        try:
            logger.info(f"Loading CUDA LLM model: {self.model_id} (dtype: {self._dtype})")

            # 在线程池中加载模型
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, self._load_sync)

            if success:
                self._is_loaded = True
                logger.info(f"CUDA LLM model loaded: {self.model_name} on {self._device}")
            else:
                logger.error(f"Failed to load CUDA LLM model: {self.model_name}")

            return success

        except Exception as e:
            logger.error(f"Error loading CUDA LLM model: {e}")
            raise LLMEngineError(f"Failed to load CUDA model: {e}")
        finally:
            self._is_loading = False

    def _load_sync(self) -> bool:
        """同步加载模型"""
        try:
            import torch

            if not torch.cuda.is_available():
                logger.error("CUDA not available")
                return False

            self._device = torch.device("cuda:0")

            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

            # 加载 tokenizer
            logger.info(f"Loading tokenizer: {self.model_id}")
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_id,
                trust_remote_code=True,
            )

            # 配置量化
            quantization_config = None
            torch_dtype = torch.float16

            if self._dtype == "int8":
                quantization_config = BitsAndBytesConfig(
                    load_in_8bit=True,
                )
                logger.info("Using int8 quantization")
            elif self._dtype == "int4":
                quantization_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                )
                logger.info("Using int4 quantization")

            # 尝试 Flash Attention 2
            attn_impl = "eager"
            try:
                import flash_attn

                attn_impl = "flash_attention_2"
                logger.info("Flash Attention 2 available")
            except ImportError:
                logger.info("Flash Attention 2 not available, using default attention")

            # 加载模型
            logger.info(f"Loading model: {self.model_id}")
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_id,
                torch_dtype=torch_dtype,
                device_map="auto",
                quantization_config=quantization_config,
                attn_implementation=attn_impl,
                trust_remote_code=True,
            )

            # 验证模型在 GPU 上
            first_param = next(self._model.parameters())
            logger.info(f"Model loaded on device: {first_param.device}")

            return True

        except ImportError as e:
            logger.error(f"Missing dependency: {e}")
            return False
        except Exception as e:
            logger.error(f"CUDA load error: {e}")
            return False

    async def unload(self) -> None:
        """卸载模型"""
        if self._model is not None:
            del self._model
            self._model = None
        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None

        # 清理 GPU 内存
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        self._is_loaded = False
        logger.info(f"CUDA LLM model unloaded: {self.model_name}")

    async def generate(self, prompt: str, max_tokens: int = 256) -> str:
        """生成文本"""
        if not self._is_loaded:
            raise LLMEngineError("Model not loaded")

        try:
            # 在线程池中生成
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self._generate_sync,
                prompt,
                max_tokens,
            )

            return response

        except Exception as e:
            logger.error(f"CUDA generate error: {e}")
            raise LLMEngineError(f"Generation failed: {e}")

    def _generate_sync(self, prompt: str, max_tokens: int) -> str:
        """同步生成文本"""
        import torch

        # 编码输入
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)

        # 生成
        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=False,
                temperature=0.0,
            )

        # 解码输出
        response = self._tokenizer.decode(outputs[0], skip_special_tokens=True)

        # 移除输入提示
        if response.startswith(prompt):
            response = response[len(prompt) :]

        return response.strip()

    def get_info(self) -> dict:
        """获取引擎信息"""
        info = super().get_info()
        info["backend"] = "cuda"
        info["dtype"] = self._dtype
        info["device"] = str(self._device) if self._device else "unloaded"
        info["requires"] = "NVIDIA GPU + transformers + torch"
        return info


def is_available() -> bool:
    """检查 CUDA 引擎是否可用"""
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False


def get_model_config(model_name: str) -> Optional[dict]:
    """获取模型配置"""
    return CUDALLMEngine.MODEL_CONFIGS.get(model_name)


def list_models() -> list:
    """列出所有可用的 CUDA LLM 模型"""
    return list(CUDALLMEngine.MODEL_CONFIGS.keys())
