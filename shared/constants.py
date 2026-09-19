"""
Voice Input Framework - 共享常量

集中管理跨模块使用的魔法数字/常量(L1:消除散落的硬编码值)。
"""

# 音频采样率(所有 STT 引擎统一使用)
AUDIO_SAMPLE_RATE = 16000

# 服务端口默认值
DEFAULT_STT_PORT = 6544
DEFAULT_LLM_PORT = 6545

# 服务默认绑定地址:本地回环。两个服务都没有鉴权,默认不应暴露到局域网;
# 需要跨机访问时用 VIF_STT_HOST / VIF_LLM_HOST 显式覆盖。
DEFAULT_BIND_HOST = "127.0.0.1"

# CORS 默认放行来源(本地 GUI / 前端开发服务器)。用 VIF_CORS_ORIGINS 覆盖(逗号分隔)。
DEFAULT_CORS_ORIGINS = [
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "http://tauri.localhost",
]

# 请求体积上限(避免单个请求撑爆内存)
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # /transcribe、/diarize 上传音频上限 100MB
WS_MAX_MESSAGE_SIZE = 16 * 1024 * 1024  # 单条 WebSocket 音频帧解码后上限 16MB
MAX_PROCESS_TEXT_LENGTH = 32768  # /process 输入文本字符数上限
