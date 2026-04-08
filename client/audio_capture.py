"""
Voice Input Framework - 音频采集模块

使用 sounddevice 进行实时音频采集，支持 VAD（语音活动检测）。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad

logger = logging.getLogger(__name__)


@dataclass
class AudioCaptureConfig:
    """音频采集配置"""
    sample_rate: int = 16000
    """采样率（Hz）"""
    channels: int = 1
    """声道数"""
    sample_width: int = 2
    """采样宽度（字节）"""
    chunk_size: int = 1024
    """每次读取的帧数"""
    vad_aggressiveness: int = 3
    """VAD 激进程度（0-3）"""
    vad_enabled: bool = True
    """是否启用 VAD"""
    vad_frame_duration: float = 0.03
    """VAD 帧时长（秒）"""


class AudioCaptureError(Exception):
    """音频采集错误"""
    pass


class AudioCapture:
    """
    音频采集器

    实时采集麦克风音频，支持语音活动检测（VAD）。

    使用示例：
        ```python
        config = AudioCaptureConfig(sample_rate=16000, vad_enabled=True)
        capture = AudioCapture(config)

        async for chunk in capture.capture():
            process_audio(chunk)
        ```

    Attributes:
        config: 音频采集配置
    """

    def __init__(self, config: Optional[AudioCaptureConfig] = None):
        """
        初始化音频采集器

        Args:
            config: 音频采集配置
        """
        self.config = config or AudioCaptureConfig()
        self._is_recording = False
        self._stream: Optional[sd.InputStream] = None
        self._vad: Optional[webrtcvad.Vad] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._buffer = bytearray()

        # 初始化 VAD
        if self.config.vad_enabled:
            self._init_vad()

    def _init_vad(self) -> None:
        """初始化 WebRTC VAD"""
        try:
            self._vad = webrtcvad.Vad(self.config.vad_aggressiveness)
        except Exception as e:
            logger.warning(f"Failed to initialize VAD: {e}, disabling VAD")
            self.config.vad_enabled = False

    def is_recording(self) -> bool:
        """检查是否正在录音"""
        return self._is_recording

    def start(self) -> None:
        """
        开始录音

        启动音频采集并填充队列。应在异步上下文中使用。

        Raises:
            AudioCaptureError: 启动失败
        """
        if self._is_recording:
            logger.warning("Already recording")
            return

        try:
            self._stream = sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                dtype='int16',
                blocksize=self.config.chunk_size,
                callback=self._audio_callback
            )
            self._stream.start()
            self._is_recording = True
            logger.info("Audio capture started")

        except Exception as e:
            logger.error(f"Failed to start audio capture: {e}")
            raise AudioCaptureError(f"Failed to start: {e}")

    def stop(self) -> None:
        """
        停止录音

        停止音频采集并关闭流。
        """
        if not self._is_recording:
            return

        self._is_recording = False

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # 清空缓冲区
        self._buffer.clear()

        logger.info("Audio capture stopped")

    def _audio_callback(self, indata: np.ndarray, frames: int, status: sd.CallbackFlags) -> None:
        """
        音频数据回调

        将采集到的音频数据放入队列。
        """
        if status:
            logger.warning(f"Audio callback status: {status}")

        # 转换为 bytes
        audio_data = indata.tobytes()

        # 如果启用 VAD，进行语音检测
        if self.config.vad_enabled and self._vad:
            try:
                # WebRTC VAD 需要 10ms, 20ms 或 30ms 的帧
                frame_size = int(self.config.sample_rate * self.config.vad_frame_duration)
                for i in range(0, len(audio_data), frame_size * 2):  # 2 bytes per sample
                    frame = audio_data[i:i + frame_size * 2]
                    if len(frame) == frame_size * 2:
                        is_speech = self._vad.is_speech(frame, self.config.sample_rate)
                        # 将 VAD 结果存储在元数据中
                        self._queue.put_nowait((frame, is_speech))
            except Exception as e:
                logger.debug(f"VAD error: {e}")
                self._queue.put_nowait((audio_data, True))  # 默认当作语音
        else:
            self._queue.put_nowait((audio_data, True))

    async def capture(self) -> AsyncIterator[bytes]:
        """
        异步音频采集生成器

        Yields:
            bytes: 音频数据块
        """
        self.start()
        try:
            while self._is_recording:
                try:
                    audio_data, is_speech = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0
                    )
                    if is_speech:
                        yield audio_data
                except asyncio.TimeoutError:
                    continue
        finally:
            self.stop()

    async def capture_with_vad(self) -> AsyncIterator[tuple[bytes, bool]]:
        """
        异步音频采集生成器（带 VAD 结果）

        Yields:
            tuple[bytes, bool]: (音频数据, 是否检测到语音)
        """
        self.start()
        try:
            while self._is_recording:
                try:
                    result = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0
                    )
                    yield result
                except asyncio.TimeoutError:
                    continue
        finally:
            self.stop()

    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.stop()

    async def __aenter__(self):
        """异步上下文管理器入口"""
        self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        self.stop()
