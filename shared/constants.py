"""
Voice Input Framework - 共享常量

集中管理跨模块使用的魔法数字/常量(L1:消除散落的硬编码值)。
"""

# 音频采样率(所有 STT 引擎统一使用)
AUDIO_SAMPLE_RATE = 16000

# 服务端口默认值
DEFAULT_STT_PORT = 6544
DEFAULT_LLM_PORT = 6545
