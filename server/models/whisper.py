"""
Voice Input Framework - Whisper STT 引擎实现

基于 transformers 库的 Whisper 模型实现。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import io
import numpy as np

# Lazy imports for optional heavy dependencies
torch = None
pipeline = None


def _ensure_torch():
    global torch, pipeline
    if torch is None:
        import torch as _torch
        from transformers import pipeline as _pipeline

        torch = _torch
        pipeline = _pipeline


# 尝试导入音频解码库
try:
    import soundfile as sf
except ImportError:
    sf = None
try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

from server.models.base import BaseSTTEngine, STTEngineError  # noqa: E402
from shared.data_types import TranscriptionResult  # noqa: E402

logger = logging.getLogger(__name__)


class WhisperEngine(BaseSTTEngine):
    """Whisper STT 引擎"""

    MODEL_CONFIGS = {
        "whisper-large-v3": {
            "name": "openai/whisper-large-v3",
            "memory_gb": 10,
        },
        "whisper-small": {
            "name": "openai/whisper-small",
            "memory_gb": 2,
        },
    }

    def __init__(self, model_name: str = "whisper-large-v3", **kwargs):
        super().__init__(model_name, **kwargs)
        self._pipeline = None
        self.model_config = self.MODEL_CONFIGS.get(
            model_name, self.MODEL_CONFIGS["whisper-large-v3"]
        )

    async def load(self) -> None:
        if self._is_loaded:
            return

        logger.info(f"Loading Whisper model: {self.model_name}")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_sync)
            self._is_loaded = True
        except Exception as e:
            raise STTEngineError(f"Failed to load model: {e}")

    def _load_sync(self):
        _ensure_torch()
        device = self.detect_device()
        dtype = torch.float16 if device == "cuda" else torch.float32

        self._pipeline = pipeline(
            "automatic-speech-recognition",
            model=self.model_config["name"],
            torch_dtype=dtype,
            device=device,
        )

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._pipeline = None
        self._is_loaded = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _convert_audio(self, audio_data: bytes, target_sr: int = 16000) -> np.ndarray:
        """将音频字节解码为 16kHz mono float32 数组。
        支持 WAV/MP3/M4A/FLAC/OGG 等格式（通过 soundfile/pydub 解码）。
        """
        # 优先使用 soundfile（支持 WAV/FLAC/OGG 等）
        if sf is not None:
            try:
                with io.BytesIO(audio_data) as buf:
                    data, sr = sf.read(buf)
                if len(data.shape) > 1:
                    data = data.mean(axis=1)  # stereo → mono
                if sr != target_sr:
                    # 简单重采样（线性插值）
                    from scipy import signal

                    data = signal.resample(data, int(len(data) * target_sr / sr))
                return data.astype(np.float32)
            except Exception:
                pass

        # 次选 pydub（支持 MP3/M4A/WAV 等更多格式）
        if AudioSegment is not None:
            try:
                seg = AudioSegment.from_file(io.BytesIO(audio_data))
                seg = seg.set_frame_rate(target_sr).set_channels(1)
                raw = seg.raw_data
                return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            except Exception:
                pass

        # 最后 fallback：假设是原始 PCM 16bit 16kHz mono
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        return audio_array.astype(np.float32) / 32768.0

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        if not self._is_loaded:
            await self.load()

        loop = asyncio.get_event_loop()
        audio_array = self._convert_audio(audio_data)

        def _do():
            return self._pipeline(
                audio_array,
                return_timestamps=True,  # 支持超过30秒的长音频
                generate_kwargs={"language": language if language != "auto" else None},
            )

        result = await loop.run_in_executor(None, _do)
        text = result.get("text", "").strip()

        return TranscriptionResult(
            text=text,
            confidence=1.0,
            language=language,
            is_final=True,
        )

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> AsyncIterator[TranscriptionResult]:
        if not self._is_loaded:
            await self.load()

        buffer = []
        async for chunk in audio_stream:
            buffer.append(chunk)
            if len(buffer) >= 10:
                combined = b"".join(buffer)
                result = await self.transcribe(combined, language, sample_rate)
                if result.text:
                    yield result
                buffer = []

        if buffer:
            combined = b"".join(buffer)
            result = await self.transcribe(combined, language, sample_rate)
            if result.text:
                yield result
