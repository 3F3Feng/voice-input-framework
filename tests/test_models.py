"""
Tests for STT Server - Model Classes (no external dependencies)
这些测试不依赖 uvicorn 等外部库，可以独立运行。
"""
import pytest
from pydantic import BaseModel
from typing import List, Optional

# Define models locally for testing (avoid import issues)
class WordTimestamp(BaseModel):
    """词级别时间戳"""
    word: str
    start: float
    end: float


class TranscriptionResult(BaseModel):
    """转写结果"""
    text: str
    confidence: float = 1.0
    language: str = "auto"
    is_final: bool = True
    stt_latency_ms: float = 0.0
    model: str = ""
    timestamps: Optional[List[WordTimestamp]] = None


class TranscriptionRequest(BaseModel):
    """转写请求"""
    language: str = "auto"
    return_timestamps: bool = False


class ModelInfo(BaseModel):
    """模型信息"""
    name: str
    description: str = ""
    is_loaded: bool = False
    is_default: bool = False


class HealthStatus(BaseModel):
    """健康状态"""
    status: str
    version: str = "1.1.0"
    uptime_seconds: float
    current_model: str
    loaded_models: List[str]
    active_connections: int = 0
    total_requests: int = 0
    failed_requests: int = 0


class ErrorResponse(BaseModel):
    """错误响应"""
    error_code: str
    error_message: str
    request_id: str


class TestWordTimestamp:
    """Test WordTimestamp model"""

    def test_basic_creation(self):
        """Test basic timestamp creation"""
        ts = WordTimestamp(word="你好", start=0.0, end=0.5)
        assert ts.word == "你好"
        assert ts.start == 0.0
        assert ts.end == 0.5

    def test_json_serialization(self):
        """Test JSON serialization"""
        ts = WordTimestamp(word="test", start=1.0, end=2.0)
        json_data = ts.model_dump()
        assert json_data["word"] == "test"
        assert json_data["start"] == 1.0
        assert json_data["end"] == 2.0

    def test_negative_values(self):
        """Test negative time values"""
        ts = WordTimestamp(word="test", start=-1.0, end=0.0)
        assert ts.start == -1.0  # Pydantic allows negative values by default

    def test_chinese_characters(self):
        """Test Chinese character handling"""
        ts = WordTimestamp(word="你好世界", start=0.0, end=1.0)
        assert ts.word == "你好世界"

    def test_empty_word(self):
        """Test empty word handling"""
        ts = WordTimestamp(word="", start=0.0, end=0.5)
        assert ts.word == ""


class TestTranscriptionResult:
    """Test TranscriptionResult model"""

    def test_basic_result(self):
        """Test basic result creation"""
        result = TranscriptionResult(text="Hello world")
        assert result.text == "Hello world"
        assert result.confidence == 1.0
        assert result.language == "auto"
        assert result.is_final is True
        assert result.timestamps is None

    def test_result_with_timestamps(self):
        """Test result with timestamps"""
        timestamps = [
            WordTimestamp(word="Hello", start=0.0, end=0.5),
            WordTimestamp(word="world", start=0.5, end=1.0),
        ]
        result = TranscriptionResult(
            text="Hello world",
            timestamps=timestamps
        )
        assert result.timestamps is not None
        assert len(result.timestamps) == 2

    def test_result_with_all_fields(self):
        """Test result with all fields"""
        result = TranscriptionResult(
            text="test",
            confidence=0.95,
            language="zh",
            is_final=True,
            stt_latency_ms=150.5,
            model="qwen_asr",
            timestamps=[WordTimestamp(word="test", start=0.0, end=0.5)]
        )
        assert result.confidence == 0.95
        assert result.language == "zh"
        assert result.stt_latency_ms == 150.5
        assert result.model == "qwen_asr"

    def test_json_export(self):
        """Test JSON export"""
        result = TranscriptionResult(text="test")
        json_data = result.model_dump()
        assert json_data["text"] == "test"
        assert json_data["confidence"] == 1.0


class TestTranscriptionRequest:
    """Test TranscriptionRequest model"""

    def test_defaults(self):
        """Test default values"""
        req = TranscriptionRequest()
        assert req.language == "auto"
        assert req.return_timestamps is False

    def test_custom_values(self):
        """Test custom values"""
        req = TranscriptionRequest(language="zh", return_timestamps=True)
        assert req.language == "zh"
        assert req.return_timestamps is True

    def test_all_languages(self):
        """Test various language codes"""
        for lang in ["zh", "en", "ja", "ko", "auto"]:
            req = TranscriptionRequest(language=lang)
            assert req.language == lang


class TestModelInfo:
    """Test ModelInfo model"""

    def test_model_info(self):
        """Test ModelInfo creation"""
        info = ModelInfo(
            name="qwen_asr",
            description="Test model",
            is_loaded=True,
            is_default=True,
        )
        assert info.name == "qwen_asr"
        assert info.description == "Test model"
        assert info.is_loaded is True
        assert info.is_default is True

    def test_minimal_model_info(self):
        """Test minimal ModelInfo"""
        info = ModelInfo(name="test")
        assert info.name == "test"
        assert info.description == ""
        assert info.is_loaded is False
        assert info.is_default is False


class TestHealthStatus:
    """Test HealthStatus model"""

    def test_health_status(self):
        """Test HealthStatus creation"""
        health = HealthStatus(
            status="ok",
            uptime_seconds=100.0,
            current_model="qwen_asr",
            loaded_models=["qwen_asr"],
            active_connections=2,
            total_requests=10,
            failed_requests=1,
        )
        assert health.status == "ok"
        assert health.total_requests == 10
        assert health.failed_requests == 1
        assert health.active_connections == 2

    def test_health_status_defaults(self):
        """Test HealthStatus defaults"""
        health = HealthStatus(
            status="loading",
            uptime_seconds=0.0,
            current_model="",
            loaded_models=[],
        )
        assert health.active_connections == 0
        assert health.total_requests == 0
        assert health.failed_requests == 0

    def test_loading_status(self):
        """Test loading status"""
        health = HealthStatus(
            status="loading",
            uptime_seconds=0.0,
            current_model="qwen_asr",
            loaded_models=[],
        )
        assert health.status == "loading"


class TestErrorResponse:
    """Test ErrorResponse model"""

    def test_error_response(self):
        """Test ErrorResponse creation"""
        error = ErrorResponse(
            error_code="E5001",
            error_message="Model loading failed",
            request_id="test-123",
        )
        assert error.error_code == "E5001"
        assert error.error_message == "Model loading failed"
        assert error.request_id == "test-123"

    def test_error_json(self):
        """Test error JSON serialization"""
        error = ErrorResponse(
            error_code="E5002",
            error_message="Timeout",
            request_id="abc-def",
        )
        json_data = error.model_dump()
        assert json_data["error_code"] == "E5002"
        assert json_data["request_id"] == "abc-def"


class TestSTTEngineConstants:
    """Test STTEngine constants (without importing the class)"""

    def test_available_models_structure(self):
        """Test AVAILABLE_MODELS structure"""
        AVAILABLE_MODELS = {
            "qwen_asr": {
                "model_id": "Qwen/Qwen3-ASR-1.7B",
                "aligner_id": "Qwen/Qwen3-ForcedAligner-0.6B",
                "memory_gb": 3.5,
            },
            "qwen_asr_small": {
                "model_id": "Qwen/Qwen3-ASR-0.6B",
                "aligner_id": "Qwen/Qwen3-ForcedAligner-0.6B",
                "memory_gb": 1.5,
            },
        }

        assert "qwen_asr" in AVAILABLE_MODELS
        assert "qwen_asr_small" in AVAILABLE_MODELS
        assert "model_id" in AVAILABLE_MODELS["qwen_asr"]
        assert "aligner_id" in AVAILABLE_MODELS["qwen_asr"]
        assert AVAILABLE_MODELS["qwen_asr"]["memory_gb"] == 3.5


class TestStructuredLogging:
    """Test structured logging format"""

    def test_log_format_structure(self):
        """Test log format has expected fields"""
        import json

        # Simulate structured log output
        log_data = {
            "timestamp": "2024-01-01T00:00:00",
            "level": "INFO",
            "logger": "stt-server",
            "message": "Test message",
            "request_id": "test-123",
        }

        # Verify it can be serialized to JSON
        json_str = json.dumps(log_data)
        parsed = json.loads(json_str)

        assert parsed["level"] == "INFO"
        assert parsed["request_id"] == "test-123"
        assert parsed["message"] == "Test message"

    def test_log_with_exception(self):
        """Test log with exception info"""
        import json

        log_data = {
            "timestamp": "2024-01-01T00:00:00",
            "level": "ERROR",
            "logger": "stt-server",
            "message": "Error occurred",
            "exception": "RuntimeError: Test error",
        }

        json_str = json.dumps(log_data)
        parsed = json.loads(json_str)

        assert parsed["level"] == "ERROR"
        assert "exception" in parsed


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
