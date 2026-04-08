"""
Voice Input Framework - 音频处理模块

提供音频降噪、分块、格式转换和端点检测功能。
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AudioProcessorConfig:
    """音频处理配置"""
    target_sample_rate: int = 16000
    """目标采样率"""
    target_channels: int = 1
    """目标声道数"""
    target_sample_width: int = 2
    """目标采样宽度（字节）"""
    chunk_duration: float = 0.1
    """分块时长（秒）"""
    enable_denoise: bool = False
    """是否启用降噪"""
    endpoint_duration: float = 0.5
    """端点检测静默时长（秒）"""
    min_audio_duration: float = 0.3
    """最小音频时长（秒）"""


class AudioProcessor:
    """
    音频处理器

    提供音频降噪、分块、格式转换和端点检测功能。

    使用示例：
        ```python
        config = AudioProcessorConfig(enable_denoise=True)
        processor = AudioProcessor(config)

        async for chunk in processor.process_audio(audio_stream):
            send_to_server(chunk)
        ```

    Attributes:
        config: 音频处理配置
    """

    def __init__(self, config: Optional[AudioProcessorConfig] = None):
        """
        初始化音频处理器

        Args:
            config: 音频处理配置
        """
        self.config = config or AudioProcessorConfig()
        self._buffer = bytearray()
        self._last_speech_time: Optional[float] = None
        self._denoise_model = None

        if self.config.enable_denoise:
            self._init_denoise()

    def _init_denoise(self) -> None:
        """初始化降噪模型"""
        try:
            import noisereduce
            self._denoise_model = noisereduce
            logger.info("Noise reduction enabled")
        except ImportError:
            logger.warning("noisereduce not installed, disabling denoise")
            self.config.enable_denoise = False

    def _bytes_to_array(self, audio_bytes: bytes) -> np.ndarray:
        """将 bytes 转换为 numpy 数组"""
        return np.frombuffer(audio_bytes, dtype=np.int16)

    def _array_to_bytes(self, audio_array: np.ndarray) -> bytes:
        """将 numpy 数组转换为 bytes"""
        if audio_array.dtype != np.int16:
            audio_array = (audio_array * 32767).astype(np.int16)
        return audio_array.tobytes()

    def resample(self, audio_bytes: bytes, from_rate: int, to_rate: int) -> bytes:
        """
        重采样

        Args:
            audio_bytes: 原始音频数据
            from_rate: 原始采样率
            to_rate: 目标采样率

        Returns:
            bytes: 重采样后的音频数据
        """
        if from_rate == to_rate:
            return audio_bytes

        try:
            import scipy.signal as signal

            audio_array = self._bytes_to_array(audio_bytes)
            num_samples = int(len(audio_array) * to_rate / from_rate)
            resampled = signal.resample(audio_array, num_samples)

            return self._array_to_bytes(resampled)
        except Exception as e:
            logger.error(f"Resampling failed: {e}")
            return audio_bytes

    def convert_stereo_to_mono(self, audio_bytes: bytes) -> bytes:
        """
        立体声转单声道

        Args:
            audio_bytes: 立体声音频数据

        Returns:
            bytes: 单声道音频数据
        """
        audio_array = self._bytes_to_array(audio_bytes)
        if len(audio_array) % 2 != 0:
            logger.warning("Audio data length is odd, cannot convert to mono")
            return audio_bytes

        # 重新整形为 (samples, 2) 然后取平均
        stereo = audio_array.reshape(-1, 2)
        mono = np.mean(stereo, axis=1).astype(np.int16)

        return mono.tobytes()

    def normalize_volume(self, audio_bytes: bytes, target_db: float = -3.0) -> bytes:
        """
        音量标准化

        Args:
            audio_bytes: 音频数据
            target_db: 目标音量（dB）

        Returns:
            bytes: 标准化后的音频数据
        """
        audio_array = self._bytes_to_array(audio_bytes).astype(np.float32)

        # 计算当前音量
        rms = np.sqrt(np.mean(audio_array ** 2))
        if rms < 1e-6:
            return audio_bytes

        # 计算需要调整的增益
        current_db = 20 * np.log10(rms)
        gain = 10 ** ((target_db - current_db) / 20)

        # 调整音量
        audio_array = audio_array * gain
        audio_array = np.clip(audio_array, -32768, 32767)

        return audio_array.astype(np.int16).tobytes()

    def denoise(self, audio_bytes: bytes, sample_rate: int) -> bytes:
        """
        降噪

        Args:
            audio_bytes: 音频数据
            sample_rate: 采样率

        Returns:
            bytes: 降噪后的音频数据
        """
        if not self.config.enable_denoise or self._denoise_model is None:
            return audio_bytes

        try:
            audio_array = self._bytes_to_array(audio_bytes).astype(np.float32) / 32768.0

            # 使用 noisereduce 进行降噪
            reduced = self._denoise_model.reduce_noise(
                y=audio_array,
                sr=sample_rate,
                stationary=False,
                prop_decrease=0.8
            )

            return (reduced * 32767).astype(np.int16).tobytes()
        except Exception as e:
            logger.error(f"Denoising failed: {e}")
            return audio_bytes

    def split_into_chunks(self, audio_bytes: bytes) -> list[bytes]:
        """
        将音频数据分割成块

        Args:
            audio_bytes: 音频数据

        Returns:
            list[bytes]: 音频块列表
        """
        bytes_per_sample = self.config.target_sample_width
        bytes_per_chunk = int(
            self.config.target_sample_rate *
            self.config.chunk_duration *
            bytes_per_sample
        )

        chunks = []
        for i in range(0, len(audio_bytes), bytes_per_chunk):
            chunk = audio_bytes[i:i + bytes_per_chunk]
            if len(chunk) == bytes_per_chunk:
                chunks.append(chunk)

        return chunks

    async def process(self, audio_stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """
        异步处理音频流

        Args:
            audio_stream: 音频流

        Yields:
            bytes: 处理后的音频块
        """
        async for chunk in audio_stream:
            # 格式转换
            processed = self._ensure_format(chunk)
            yield processed

    def _ensure_format(self, audio_bytes: bytes) -> bytes:
        """确保音频格式正确"""
        audio_array = self._bytes_to_array(audio_bytes)

        # 如果是浮点数据，转换为 int16
        if audio_array.dtype == np.float32 or audio_array.dtype == np.float64:
            audio_array = (audio_array * 32767).astype(np.int16)
            return audio_array.tobytes()

        return audio_bytes

    def detect_endpoint(
        self,
        audio_bytes: bytes,
        is_speech: bool,
        current_time: float
    ) -> tuple[bool, Optional[bytes]]:
        """
        端点检测

        检测一句话是否结束（根据静默时长判断）。

        Args:
            audio_bytes: 音频数据
            is_speech: 当前块是否包含语音
            current_time: 当前时间戳

        Returns:
            tuple[bool, Optional[bytes]]: (是否结束, 累积的完整音频)
        """
        bytes_per_sample = self.config.target_sample_width
        chunk_duration = len(audio_bytes) / (self.config.target_sample_rate * bytes_per_sample)

        if is_speech:
            self._last_speech_time = current_time
            self._buffer.extend(audio_bytes)
            return False, None
        else:
            # 静默检测
            if self._last_speech_time is None:
                self._buffer.extend(audio_bytes)
                return False, None

            silence_duration = current_time - self._last_speech_time

            # 如果静默时长超过阈值，认为一句话结束
            if silence_duration >= self.config.endpoint_duration:
                # 检查缓冲区是否有足够的音频
                min_size = int(
                    self.config.target_sample_rate *
                    self.config.min_audio_duration *
                    bytes_per_sample
                )

                if len(self._buffer) >= min_size:
                    result = bytes(self._buffer)
                    self._buffer.clear()
                    self._last_speech_time = None
                    return True, result
                else:
                    self._buffer.clear()
                    self._last_speech_time = None
                    return False, None
            else:
                self._buffer.extend(audio_bytes)
                return False, None

    def flush(self) -> Optional[bytes]:
        """
        刷新缓冲区

        返回缓冲区中剩余的所有音频数据。

        Returns:
            Optional[bytes]: 缓冲区中的音频数据
        """
        if self._buffer:
            result = bytes(self._buffer)
            self._buffer.clear()
            self._last_speech_time = None
            return result
        return None

    def reset(self) -> None:
        """重置处理器状态"""
        self._buffer.clear()
        self._last_speech_time = None
