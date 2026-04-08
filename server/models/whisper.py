"""
Voice Input Framework - Whisper STT 引擎实现

基于 transformers 库的 Whisper 模型实现。
"""

import asyncio
import io
import logging
from collections.abc import AsyncIterator
from typing import Optional

import numpy as np
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from voice_input_framework.server.models.base import (
    BaseSTTEngine,
    InferenceError,
    ModelNotLoadedError,
)
from voice_input_framework.shared.types import TranscriptionResult

logger = logging.getLogger(__name__)


class WhisperEngine(BaseSTTEngine):
    """
    Whisper STT 引擎

    使用 transformers 库实现 Whisper 语音识别。

    Attributes:
        model_path: 模型路径或 HuggingFace ID
        device: 运行设备
        language: 默认语言
        compute_type: 计算精度
    """

    SUPPORTED_LANGUAGES = [
        "zh", "en", "ja", "ko", "de", "es", "fr", "it", "pt", "ru",
        "ar", "hi", "vi", "th", "id", "ms", "tr", "pl", "nl", "uk",
    ]

    def __init__(
        self,
        model_path: str = "openai/whisper-large-v3",
        device: str = "auto",
        language: str = "auto",
        compute_type: str = "float16",
        **kwargs
    ):
        """
        初始化 Whisper 引擎

        Args:
            model_path: Whisper 模型路径或 ID
            device: 运行设备
            language: 默认语言
            compute_type: 计算精度（float16/float32/int8）
            **kwargs: 额外参数
        """
        super().__init__(model_path, device, language, **kwargs)
        self.compute_type = compute_type
        self._pipe = None
        self._model = None
        self._processor = None

    def _get_supported_languages(self) -> list[str]:
        """获取支持的语言列表"""
        return self.SUPPORTED_LANGUAGES

    def _get_description(self) -> str:
        """获取引擎描述"""
        return f"Whisper ASR ({self.model_path}) - OpenAI's speech recognition model"

    def _get_device(self) -> str:
        """确定实际使用的设备"""
        if self.device == "auto":
            if torch.cuda.is_available():
                return "cuda:0"
            elif torch.backends.mps.is_available():
                return "mps"
            else:
                return "cpu"
        return self.device

    def _get_torch_dtype(self) -> torch.dtype:
        """获取 PyTorch 数据类型"""
        dtype_map = {
            "float16": torch.float16,
            "float32": torch.float32,
            "int8": torch.int8,
        }
        return dtype_map.get(self.compute_type, torch.float16)

    async def load_model(self) -> None:
        """
        加载 Whisper 模型

        异步加载模型到 GPU/CPU 内存。
        """
        if self._is_loaded:
            logger.info("Model already loaded")
            return

        logger.info(f"Loading Whisper model: {self.model_path}")

        try:
            # 在线程池中执行模型加载（避免阻塞事件循环）
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_model_sync)

            self._is_loaded = True
            logger.info("Whisper model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise ModelNotLoadedError(f"Failed to load Whisper model: {e}")

    def _load_model_sync(self) -> None:
        """同步加载模型（在单独线程中执行）"""
        device = self._get_device()
        torch_dtype = self._get_torch_dtype()

        logger.info(f"Loading model on {device} with {torch_dtype}")

        self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
            self.model_path,
            torch_dtype=torch_dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
        )
        self._model.to(device)

        self._processor = AutoProcessor.from_pretrained(self.model_path)

        self._pipe = pipeline(
            "automatic-speech-recognition",
            model=self._model,
            tokenizer=self._processor.tokenizer,
            feature_extractor=self._processor.feature_extractor,
            max_new_tokens=128,
            chunk_length_s=30,
            batch_size=16,
            return_timestamps=True,
            torch_dtype=torch_dtype,
            device=device,
        )

    async def unload_model(self) -> None:
        """卸载模型，释放内存"""
        if not self._is_loaded:
            return

        logger.info("Unloading Whisper model")

        self._pipe = None
        self._model = None
        self._processor = None

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        self._is_loaded = False
        logger.info("Model unloaded")

    async def transcribe(
        self,
        audio: bytes,
        language: Optional[str] = None,
        **kwargs
    ) -> TranscriptionResult:
        """
        同步转写

        Args:
            audio: PCM 格式音频数据
            language: 语言提示
            **kwargs: 额外参数

        Returns:
            TranscriptionResult: 转写结果
        """
        self._ensure_loaded()

        lang = language or self.language
        if lang == "auto":
            lang = None

        try:
            # 转换音频数据为 numpy 数组
            audio_array = self._bytes_to_array(audio)

            # 在线程池中执行推理
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._pipe(
                    audio_array,
                    generate_kwargs={"language": lang} if lang else {},
                )
            )

            text = result.get("text", "").strip()
            chunks = result.get("chunks", [])

            # 计算置信度（基于分块数量估算）
            confidence = self._calculate_confidence(chunks)

            return TranscriptionResult(
                text=text,
                confidence=confidence,
                language=lang or "auto",
                is_final=True,
                metadata={
                    "chunks": chunks,
                    "model": self.model_path,
                }
            )

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise InferenceError(f"Transcription failed: {e}")

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: Optional[str] = None,
        **kwargs
    ) -> AsyncIterator[TranscriptionResult]:
        """
        流式转写

        缓冲音频数据并在达到一定长度后进行转写。

        Args:
            audio_stream: 音频流
            language: 语言提示
            **kwargs: 额外参数

        Yields:
            TranscriptionResult: 转写结果
        """
        self._ensure_loaded()

        lang = language or self.language
        if lang == "auto":
            lang = None

        # 音频缓冲区
        buffer = bytearray()
        sample_rate = kwargs.get("sample_rate", 16000)
        bytes_per_sample = 2  # 16-bit
        chunk_duration = kwargs.get("chunk_duration", 3.0)

        try:
            async for audio_chunk in audio_stream:
                buffer.extend(audio_chunk)

                # 计算当前缓冲区时长
                current_duration = len(buffer) / (sample_rate * bytes_per_sample)

                # 达到阈值时进行转写
                if current_duration >= chunk_duration:
                    audio_array = self._bytes_to_array(bytes(buffer))

                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        lambda: self._pipe(
                            audio_array,
                            generate_kwargs={"language": lang} if lang else {},
                        )
                    )

                    yield TranscriptionResult(
                        text=result.get("text", "").strip(),
                        confidence=1.0,
                        language=lang or "auto",
                        is_final=False,
                    )

            # 最终转写
            if buffer:
                audio_array = self._bytes_to_array(bytes(buffer))
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._pipe(
                        audio_array,
                        generate_kwargs={"language": lang} if lang else {},
                    )
                )
                yield TranscriptionResult(
                    text=result.get("text", "").strip(),
                    confidence=1.0,
                    language=lang or "auto",
                    is_final=True,
                )

        except Exception as e:
            logger.error(f"Streaming transcription failed: {e}")
            raise InferenceError(f"Streaming transcription failed: {e}")

    def _bytes_to_array(self, audio_bytes: bytes) -> np.ndarray:
        """将 PCM bytes 转换为 float32 numpy 数组"""
        return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    def _calculate_confidence(self, chunks: list) -> float:
        """简单的置信度估算"""
        if not chunks:
            return 0.0
        return 1.0  # Whisper pipeline 不直接提供置信度
