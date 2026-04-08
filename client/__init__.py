"""
Voice Input Framework - 客户端模块

提供音频采集、处理和与服务端通信的功能。
"""

# 使用相对导入，避免包名问题
from .audio_capture import AudioCapturer, VADAudioCapturer, AudioConfig, list_audio_devices
from .stt_client import STTClient, StreamingSTTClient, ClientConfig

__all__ = [
    "AudioCapturer",
    "VADAudioCapturer", 
    "AudioConfig",
    "list_audio_devices",
    "STTClient",
    "StreamingSTTClient",
    "ClientConfig",
]
