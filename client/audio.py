#!/usr/bin/env python3
"""
客户端音频模块 — 录制、电平检测、设备管理

从 gui.py 拆分出来的音频相关职责：
- 音频设备枚举
- 录制启停
- 音频电平检测
- 音频缓冲区管理
"""

import logging
import queue
import threading
import time
from typing import Optional, Callable

import numpy as np

logger = logging.getLogger(__name__)

# 音频参数
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_SIZE = 1024


class AudioRecorder:
    """音频录制器 — 封装 sounddevice 录制逻辑"""

    def __init__(self, sample_rate: int = AUDIO_SAMPLE_RATE,
                 channels: int = AUDIO_CHANNELS,
                 chunk_size: int = AUDIO_CHUNK_SIZE):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size

        self._stream = None
        self._recording = False
        self._audio_buffer: list[bytes] = []
        self._audio_queue: queue.Queue = queue.Queue()
        self._record_start_time: Optional[float] = None
        self._selected_device: Optional[int] = None  # None = 默认设备

    # ──────────────────── 属性 ────────────────────

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def audio_buffer(self) -> list[bytes]:
        return self._audio_buffer

    @property
    def audio_queue(self) -> queue.Queue:
        return self._audio_queue

    @property
    def record_duration(self) -> float:
        """当前录音时长（秒）"""
        if self._record_start_time:
            return time.time() - self._record_start_time
        return 0.0

    @property
    def selected_device(self) -> Optional[int]:
        return self._selected_device

    @selected_device.setter
    def selected_device(self, device_id: Optional[int]):
        self._selected_device = device_id

    # ──────────────────── 设备管理 ────────────────────

    @staticmethod
    def get_devices() -> dict:
        """获取系统中可用的音频输入设备

        Returns:
            dict: {device_id: device_name}
        """
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            input_devices = {}
            for i, device in enumerate(devices):
                if device['max_input_channels'] > 0:
                    input_devices[i] = f"{device['name']}"
            return input_devices if input_devices else {-1: "默认设备"}
        except Exception as e:
            logger.warning(f"获取音频设备失败: {e}")
            return {-1: "默认设备"}

    # ──────────────────── 录制控制 ────────────────────

    def start_recording(self):
        """开始录音

        启动 sounddevice.InputStream，音频数据同时写入 buffer 和 queue。
        queue 用于流式发送，buffer 用于备用整段发送。
        """
        import sounddevice as sd

        if self._recording:
            logger.warning("已在录音中，忽略重复启动")
            return

        self._recording = True
        self._audio_buffer = []
        # 清空旧队列
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            if self._recording:
                audio_bytes = indata.copy().tobytes()
                self._audio_buffer.append(audio_bytes)
                try:
                    self._audio_queue.put_nowait(audio_bytes)
                except queue.Full:
                    pass  # 队列满时丢弃

        try:
            self._stream = sd.InputStream(
                device=self._selected_device,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                blocksize=self.chunk_size,
                callback=callback,
            )
            self._stream.start()
            self._record_start_time = time.time()
        except Exception as e:
            logger.error(f"启动录音失败: {e}")
            self._recording = False
            raise

    def stop_recording(self):
        """停止录音

        Returns:
            tuple: (chunks_count, duration_seconds)
        """
        self._recording = False
        chunks_count = len(self._audio_buffer)
        duration = self.record_duration

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.warning(f"关闭音频流失败: {e}")
            self._stream = None

        # 通知消费端结束
        try:
            self._audio_queue.put_nowait(None)  # None = 结束信号
        except queue.Full:
            pass

        self._record_start_time = None
        return chunks_count, duration

    # ──────────────────── 音频数据 ────────────────────

    def get_full_audio(self) -> bytes:
        """获取完整的音频 PCM 数据"""
        return b"".join(self._audio_buffer)

    # ──────────────────── 电平检测 ────────────────────

    def get_audio_level(self) -> tuple[int, int]:
        """获取当前音频电平

        Returns:
            tuple: (db_level, db_level) — 两个相同值方便 UI 使用
        """
        try:
            if not self._audio_buffer:
                return 0, 0

            last_chunk = self._audio_buffer[-1] if self._audio_buffer else b''
            if not last_chunk:
                return 0, 0

            audio_data = np.frombuffer(last_chunk, dtype=np.int16)
            if len(audio_data) == 0:
                return 0, 0

            # 计算 RMS 音量，转换为 dB
            rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
            db = min(100, max(0, int((np.log10(max(rms, 1)) / 5) * 100)))
            return db, db
        except Exception as e:
            logger.debug(f"获取音频电平失败: {e}")
            return 0, 0

    # ──────────────────── 清理 ────────────────────

    def cleanup(self):
        """清理资源"""
        if self._recording:
            try:
                self.stop_recording()
            except Exception as e:
                logger.warning(f"停止录音失败: {e}")
