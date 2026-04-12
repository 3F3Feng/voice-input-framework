"""
Voice Input Framework - Whisper.cpp STT 引擎实现

基于 whisper.cpp 的本地 Whisper 模型实现，使用 CGO bindings。
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Optional

import numpy as np

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


class WhisperCppEngine(BaseSTTEngine):
    """Whisper.cpp STT 引擎"""

    MODEL_CONFIGS = {
        "whisper-v3-base": {
            "name": "Whisper V3 Base",
            "model_path": "~/.cache/whisper/ggml-base.bin",
            "memory_gb": 1,
        },
        "whisper-v3-large": {
            "name": "Whisper V3 Large",
            "model_path": "~/.cache/whisper/large-v3.bin",
            "memory_gb": 3,
        },
    }

    def __init__(self, model_name: str = "whisper-v3-large", **kwargs):
        super().__init__(model_name, **kwargs)
        self._pipeline = None
        self.model_config = self.MODEL_CONFIGS.get(
            model_name, self.MODEL_CONFIGS["whisper-v3-large"]
        )
        self.whisper_cpp_path = Path.home() / "whisper.cpp"
        self.whisper_cli = self.whisper_cpp_path / "build" / "bin" / "whisper-cli"
        self.model_path = os.path.expanduser(self.model_config["model_path"])

    async def load(self) -> None:
        if self._is_loaded:
            return

        logger.info(f"Checking whisper.cpp CLI at: {self.whisper_cli}")
        if not self.whisper_cli.exists():
            raise STTEngineError(f"whisper.cpp CLI not found at {self.whisper_cli}")

        if not Path(self.model_path).exists():
            raise STTEngineError(f"Whisper model not found at {self.model_path}")

        logger.info(f"Whisper.cpp model ready: {self.model_config['name']}")
        self._is_loaded = True

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._is_loaded = False

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        if not self._is_loaded:
            await self.load()

        loop = asyncio.get_event_loop()
        result_text = await loop.run_in_executor(None, self._transcribe_sync, audio_data, language)

        detected_lang = language if language != "auto" else "en"
        return TranscriptionResult(
            text=result_text,
            confidence=1.0,
            language=detected_lang,
            is_final=True,
        )

    def _transcribe_sync(self, audio_data: bytes, language: str) -> str:
        """Synchronous transcription using whisper-cli"""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            audio_path = f.name

        try:
            cmd = [
                str(self.whisper_cli),
                "-m", self.model_path,
                "-f", audio_path,
                "--no-timestamps",
                "-otxt",
            ]

            if language != "auto":
                cmd.extend(["-l", language])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                logger.error(f"whisper-cli error: {result.stderr}")
                return ""

            txt_path = audio_path + ".txt"
            if Path(txt_path).exists():
                with open(txt_path, "r") as f:
                    text = f.read().strip()
                Path(txt_path).unlink()
                return text

            return result.stdout.strip()

        finally:
            Path(audio_path).unlink(missing_ok=True)

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
