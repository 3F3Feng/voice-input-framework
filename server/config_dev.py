"""
Voice Input Framework - 开发服务器配置

用于本地开发测试，使用不同端口避免与生产服务器冲突。
"""

import os
from server.config import ServerConfig, ModelConfig, LLMConfig


def get_dev_config() -> ServerConfig:
    """获取开发服务器配置"""
    config = ServerConfig()
    
    # 开发服务器使用 6544 端口
    config.host = "127.0.0.1"
    config.port = 6544
    config.debug = True
    config.log_level = "DEBUG"
    
    # STT 模型配置
    config.add_model(ModelConfig(
        name="whisper",
        model_path="openai/whisper-large-v3",
        device="auto",
        language="auto",
        compute_type="float16",
        max_audio_length=300,
    ))
    
    config.add_model(ModelConfig(
        name="whisper-small",
        model_path="openai/whisper-small",
        device="auto",
        language="auto",
        compute_type="float32",
        max_audio_length=120,
    ))
    
    config.add_model(ModelConfig(
        name="qwen_asr",
        model_path="Qwen/Qwen3-ASR-1.7B",
        device="auto",
        language="zh",
        compute_type="float16",
        max_audio_length=60,
    ))
    
    # LLM 配置 - 开发时启用
    config.llm = LLMConfig(
        enabled=True,
        default_model="Qwen3.5-0.8B-OptiQ",
        thinking_timeout=5.0,
        max_tokens=128,
    )
    
    return config


# 环境变量覆盖
def get_dev_config_from_env() -> ServerConfig:
    """从环境变量读取开发配置"""
    config = get_dev_config()
    
    if host := os.getenv("VIF_DEV_HOST"):
        config.host = host
    if port := os.getenv("VIF_DEV_PORT"):
        config.port = int(port)
    if model := os.getenv("VIF_DEV_MODEL"):
        config.default_model = model
    if log_level := os.getenv("VIF_DEV_LOG_LEVEL"):
        config.log_level = log_level
    
    # LLM 配置环境变量
    if llm_enabled := os.getenv("VIF_LLM_ENABLED"):
        config.llm.enabled = llm_enabled.lower() in ("true", "1", "yes")
    if llm_model := os.getenv("VIF_LLM_MODEL"):
        config.llm.default_model = llm_model
    
    return config
