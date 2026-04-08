"""
Voice Input Framework - 客户端模块

提供音频采集、处理和与服务端通信的功能。
"""

from voice_input_framework.client.audio_capture import AudioCapture
from voice_input_framework.client.audio_processor import AudioProcessor
from voice_input_framework.client.stt_client import STTClient

__all__ = [
    "AudioCapture",
    "AudioProcessor",
    "STTClient",
]
