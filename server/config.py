"""
Voice Input Framework - 服务端配置
"""

import os
from shared.model_registry import get_default_model
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelConfig:
    """模型配置"""
    name: str
    model_path: Optional[str] = None
    device: str = "auto"
    language: str = "auto"
    compute_type: str = "float16"
    max_audio_length: int = 60
    chunk_size: int = 30
    extra: dict = field(default_factory=dict)


@dataclass
class LLMConfig:
    """LLM后处理配置"""
    enabled: bool = True
    default_model: str = "Qwen3.5-0.8B-OptiQ"  # MLX 量化模型 (推荐)
    thinking_timeout: float = 5.0  # 思考等待超时（秒）
    max_tokens: int = 128


@dataclass
class ServerConfig:
    """服务端配置"""
    host: str = "0.0.0.0"
    port: int = 6543
    debug: bool = False
    default_model: str = get_default_model()
    models: dict[str, ModelConfig] = field(default_factory=dict)
    models_dir: str = "./models"
    auto_load_default: bool = True
    max_concurrent_requests: int = 10
    max_concurrent_streams: int = 20
    queue_timeout: int = 30
    enable_cache: bool = True
    cache_size_mb: int = 500
    cache_ttl: int = 3600
    ws_ping_interval: int = 30
    ws_ping_timeout: int = 10
    ws_max_message_size: int = 10 * 1024 * 1024
    log_level: str = "INFO"
    log_file: Optional[str] = None
    api_key: Optional[str] = None
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    llm: LLMConfig = field(default_factory=LLMConfig)  # LLM后处理配置

    @classmethod
    def from_env(cls) -> "ServerConfig":
        config = cls()
        if host := os.getenv("VIF_HOST"):
            config.host = host
        if port := os.getenv("VIF_PORT"):
            config.port = int(port)
        if model := os.getenv("VIF_DEFAULT_MODEL"):
            config.default_model = model
        if models_dir := os.getenv("VIF_MODELS_DIR"):
            config.models_dir = models_dir
        if max_concurrent := os.getenv("VIF_MAX_CONCURRENT"):
            config.max_concurrent_requests = int(max_concurrent)
        if api_key := os.getenv("VIF_API_KEY"):
            config.api_key = api_key
        if log_level := os.getenv("VIF_LOG_LEVEL"):
            config.log_level = log_level
        return config

    def get_model_config(self, model_name: str) -> Optional[ModelConfig]:
        return self.models.get(model_name)

    def add_model(self, config: ModelConfig) -> None:
        self.models[config.name] = config


def get_default_config() -> ServerConfig:
    """获取默认配置"""
    config = ServerConfig()

    # Whisper 模型
    config.add_model(ModelConfig(
        name="whisper",
        model_path="openai/whisper-large-v3",
        device="auto",
        language="auto",
        compute_type="float16",
        max_audio_length=300,
    ))

    # Whisper small
    config.add_model(ModelConfig(
        name="whisper-small",
        model_path="openai/whisper-small",
        device="auto",
        language="auto",
        compute_type="float32",
        max_audio_length=120,
    ))

    # Qwen3-ASR 1.7B
    config.add_model(ModelConfig(
        name="qwen_asr",
        model_path="Qwen/Qwen3-ASR-1.7B",
        device="auto",
        language="zh",
        compute_type="float16",
        max_audio_length=60,
    ))

    return config
