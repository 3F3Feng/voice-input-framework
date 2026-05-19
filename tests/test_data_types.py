#!/usr/bin/env python3
"""
Voice Input Framework - 数据类型测试
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.data_types import (
    TranscriptionResult,
    ErrorResponse,
    ModelInfo,
    HealthStatus,
    AudioChunk,
)


class TestTranscriptionResult:
    """TranscriptionResult 测试类"""
    
    def test_create_basic(self):
        """测试创建基本转写结果"""
        result = TranscriptionResult(
            text="Hello world",
            confidence=0.95,
            language="en",
        )
        
        assert result.text == "Hello world"
        assert result.confidence == 0.95
        assert result.language == "en"
        assert result.is_final is False  # 默认值
    
    def test_create_full(self):
        """测试创建完整转写结果"""
        result = TranscriptionResult(
            text="测试文本",
            confidence=0.88,
            language="zh",
            is_final=True,
            start_time=0.5,
            end_time=2.3,
            words=[{"word": "测试", "start": 0.5, "end": 1.0}],
            metadata={"source": "test"},
        )
        
        assert result.text == "测试文本"
        assert result.is_final is True
        assert result.start_time == 0.5
        assert result.end_time == 2.3
        assert result.words is not None
        assert result.metadata["source"] == "test"
    
    def test_to_dict(self):
        """测试转换为字典"""
        result = TranscriptionResult(
            text="Hello",
            confidence=0.9,
            language="en",
        )
        
        d = result.to_dict()
        
        assert d["text"] == "Hello"
        assert d["confidence"] == 0.9
        assert d["language"] == "en"
        assert d["is_final"] is False
    
    def test_empty_text(self):
        """测试空文本"""
        result = TranscriptionResult(text="")
        assert result.text == ""
    
    def test_default_confidence(self):
        """测试默认置信度"""
        result = TranscriptionResult(text="test")
        assert result.confidence == 1.0  # 默认值


class TestErrorResponse:
    """ErrorResponse 测试类"""
    
    def test_create_error(self):
        """测试创建错误响应"""
        error = ErrorResponse(
            error_code="E1001",
            error_message="Test error",
        )
        
        assert error.error_code == "E1001"
        assert error.error_message == "Test error"
        assert error.timestamp is not None
    
    def test_to_dict(self):
        """测试转换为字典"""
        error = ErrorResponse(
            error_code="E500",
            error_message="Server error",
            details={"line": 42},
        )
        
        d = error.to_dict()
        
        assert d["error_code"] == "E500"
        assert d["error_message"] == "Server error"
        assert d["details"]["line"] == 42


class TestModelInfo:
    """ModelInfo 测试类"""
    
    def test_create_model_info(self):
        """测试创建模型信息"""
        info = ModelInfo(
            name="whisper-large-v3",
            description="OpenAI Whisper large model",
            supported_languages=["en", "zh", "ja"],
            is_loaded=True,
            is_default=True,
            model_size_mb=3000,
            latency_ms=150,
        )
        
        assert info.name == "whisper-large-v3"
        assert len(info.supported_languages) == 3
        assert info.is_loaded is True
        assert info.model_size_mb == 3000
    
    def test_to_dict(self):
        """测试转换为字典"""
        info = ModelInfo(
            name="test-model",
            is_loaded=False,
        )
        
        d = info.to_dict()
        
        assert d["name"] == "test-model"
        assert d["is_loaded"] is False


class TestHealthStatus:
    """HealthStatus 测试类"""
    
    def test_create_health_status(self):
        """测试创建健康状态"""
        status = HealthStatus(
            status="healthy",
            version="1.0.0",
            uptime_seconds=3600.0,
            current_model="qwen_asr_mlx_native_small",
            loaded_models=["qwen_asr_mlx_native_small", "whisper_mlx_turbo"],
            active_connections=2,
            memory_usage_mb=512.5,
        )
        
        assert status.status == "healthy"
        assert status.uptime_seconds == 3600.0
        assert len(status.loaded_models) == 2
        assert status.active_connections == 2
    
    def test_to_dict(self):
        """测试转换为字典"""
        status = HealthStatus(
            status="ok",
            version="1.0.0",
            uptime_seconds=100.0,
            current_model="test",
            loaded_models=[],
        )
        
        d = status.to_dict()
        
        assert d["status"] == "ok"
        assert d["version"] == "1.0.0"


class TestAudioChunk:
    """AudioChunk 测试类"""
    
    def test_create_audio_chunk(self):
        """测试创建音频块"""
        chunk = AudioChunk(
            data=b"\x00\x01\x02\x03",
            sample_rate=16000,
            channels=1,
            sample_width=2,
        )
        
        assert chunk.sample_rate == 16000
        assert chunk.channels == 1
        assert chunk.sample_width == 2
    
    def test_duration_calculation(self):
        """测试时长计算"""
        # 2 bytes per sample, 1 channel, 16000 samples = 1 second
        data = b"\x00" * (2 * 16000)
        chunk = AudioChunk(
            data=data,
            sample_rate=16000,
            channels=1,
            sample_width=2,
        )
        
        assert chunk.duration == pytest.approx(1.0, rel=0.01)
    
    def test_duration_with_channels(self):
        """测试多通道时长计算"""
        # 2 bytes per sample, 2 channels, 16000 samples per channel = 0.5 second
        data = b"\x00" * (2 * 2 * 16000)
        chunk = AudioChunk(
            data=data,
            sample_rate=16000,
            channels=2,
            sample_width=2,
        )
        
        # Duration should still be 1 second (total samples / sample_rate)
        assert chunk.duration == pytest.approx(1.0, rel=0.01)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
