#!/usr/bin/env python3
"""
Voice Input Framework - MLX Whisper STT 引擎实现

使用 mlx-whisper 库进行语音识别，专为 MLX 环境优化。
这是 Qwen3-ASR 在 MLX 环境中的推荐替代方案。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


class WhisperMLXEngine(BaseSTTEngine):
    """MLX Whisper STT 引擎

    使用 mlx-whisper 库在 Apple Silicon 上高效运行 Whisper 模型。
    无需 PyTorch，直接使用 MLX 框架。
    """

    MODEL_CONFIGS = {
        "whisper_mlx": {
            "model_id": "mlx-community/whisper-large-v3-mlx",
            "memory_gb": 3.0,
            "description": "MLX Whisper Large V3 (推荐，多语言支持)",
        },
        "whisper_mlx_medium": {
            "model_id": "mlx-community/whisper-medium-mlx",
            "memory_gb": 1.5,
            "description": "MLX Whisper Medium (更快)",
        },
        "whisper_mlx_small": {
            "model_id": "mlx-community/whisper-small-mlx",
            "memory_gb": 0.5,
            "description": "MLX Whisper Small (最快)",
        },
        "whisper_mlx_turbo": {
            "model_id": "mlx-community/whisper-large-v3-turbo-mlx",
            "memory_gb": 2.0,
            "description": "MLX Whisper Large V3 Turbo (快速+准确)",
        },
    }

    def __init__(self, model_name: str = "whisper_mlx", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model_id = None
        self._loaded_model = None
        self.model_config = self.MODEL_CONFIGS.get(model_name, self.MODEL_CONFIGS["whisper_mlx"])

    async def load(self) -> None:
        """预加载模型（可选，mlx-whisper 会自动缓存）"""
        if self._is_loaded:
            return

        logger.info(f"Pre-loading MLX Whisper model: {self.model_config['model_id']}")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_sync)
            self._is_loaded = True
            logger.info("MLX Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise STTEngineError(f"Failed to load MLX Whisper model: {e}")

    def _load_sync(self):
        """同步预加载模型"""
        import mlx_whisper

        # 预加载模型
        model_id = self.model_config["model_id"]
        self._model_id = model_id

        # mlx-whisper 会在首次使用时自动下载和缓存模型
        # 这里我们用一个简单的测试来触发预加载
        test_audio = np.zeros(16000, dtype=np.float32)
        _ = mlx_whisper.transcribe(test_audio, path_or_hf_repo=model_id)

    async def unload(self) -> None:
        """卸载模型"""
        self._loaded_model = None
        self._is_loaded = False

        # 清理 MLX 缓存
        import mlx.core as mx
        mx.clear_cache()

    def _convert_audio(self, audio_data: bytes, sample_rate: int = 16000) -> np.ndarray:
        """将音频数据转换为 float32 numpy 数组"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_array = audio_array.astype(np.float32) / 32768.0

        # 如果需要重采样（简单线性插值）
        if sample_rate != 16000:
            target_length = int(len(audio_array) * 16000 / sample_rate)
            audio_array = np.interp(
                np.linspace(0, len(audio_array), target_length),
                np.arange(len(audio_array)),
                audio_array,
            )

        return audio_array

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        """转写音频"""
        if not self._is_loaded:
            await self.load()

        loop = asyncio.get_event_loop()
        audio_array = self._convert_audio(audio_data, sample_rate)

        def _do_transcribe():
            import mlx_whisper

            model_id = self.model_config["model_id"]

            # 设置语言参数
            language_param = None if language == "auto" else language

            # 执行转录
            # return_timestamps=True 支持超过30秒的长音频
            result = mlx_whisper.transcribe(
                audio_array,
                path_or_hf_repo=model_id,
                language=language_param,
                return_timestamps=True,
            )

            return result

        result = await loop.run_in_executor(None, _do_transcribe)

        text = result.get("text", "").strip()
        detected_lang = result.get("language", language)

        return TranscriptionResult(
            text=text,
            confidence=1.0,
            language=detected_lang,
            is_final=True,
        )

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> AsyncIterator[TranscriptionResult]:
        """流式转写"""
        if not self._is_loaded:
            await self.load()

        buffer = []
        async for chunk in audio_stream:
            buffer.append(chunk)
            # 每 10 个 chunk 处理一次（约 1-2 秒音频）
            if len(buffer) >= 10:
                combined = b"".join(buffer)
                result = await self.transcribe(combined, language, sample_rate)
                if result.text:
                    yield result
                buffer = []

        # 处理剩余数据
        if buffer:
            combined = b"".join(buffer)
            result = await self.transcribe(combined, language, sample_rate)
            if result.text:
                yield result

    def get_model_info(self) -> dict:
        """获取模型信息"""
        info = super().get_model_info()
        info.update({
            "model_id": self.model_config.get("model_id", "unknown"),
            "description": self.model_config.get("description", ""),
            "backend": "mlx-whisper",
        })
        return info


# 用于注册的别名
MLXWhisperEngine = WhisperMLXEngine
