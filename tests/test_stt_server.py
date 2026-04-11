"""
Tests for STT Server
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
import sys
import asyncio
from pathlib import Path

# Add project path
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))


class TestWordTimestamp:
    """Test WordTimestamp model"""

    def test_basic_creation(self):
        """Test basic timestamp creation"""
        from services.stt_server import WordTimestamp

        ts = WordTimestamp(word="你好", start=0.0, end=0.5)
        assert ts.word == "你好"
        assert ts.start == 0.0
        assert ts.end == 0.5

    def test_json_serialization(self):
        """Test JSON serialization"""
        from services.stt_server import WordTimestamp

        ts = WordTimestamp(word="test", start=1.0, end=2.0)
        json_data = ts.model_dump()
        assert json_data["word"] == "test"
        assert json_data["start"] == 1.0
        assert json_data["end"] == 2.0


class TestTranscriptionResult:
    """Test TranscriptionResult model"""

    def test_basic_result(self):
        """Test basic result creation"""
        from services.stt_server import TranscriptionResult

        result = TranscriptionResult(text="Hello world")
        assert result.text == "Hello world"
        assert result.confidence == 1.0
        assert result.language == "auto"
        assert result.is_final is True
        assert result.timestamps is None

    def test_result_with_timestamps(self):
        """Test result with timestamps"""
        from services.stt_server import TranscriptionResult, WordTimestamp

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


class TestSTTEngine:
    """Test STTEngine class"""

    def test_init(self):
        """Test engine initialization"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        assert engine.default_model == "qwen_asr"
        assert not engine._is_loaded
        assert not engine._aligner_loaded
        assert not engine._loading

    def test_available_models(self):
        """Test available models configuration"""
        from services.stt_server import STTEngine

        assert "qwen_asr" in STTEngine.AVAILABLE_MODELS
        assert "qwen_asr_small" in STTEngine.AVAILABLE_MODELS
        assert "model_id" in STTEngine.AVAILABLE_MODELS["qwen_asr"]
        assert "aligner_id" in STTEngine.AVAILABLE_MODELS["qwen_asr"]

    def test_is_loading(self):
        """Test loading state"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        assert not engine.is_loading()

        engine._loading = True
        assert engine.is_loading()

    def test_is_model_loaded(self):
        """Test model loaded state"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        assert not engine.is_model_loaded()

        engine._is_loaded = True
        assert engine.is_model_loaded()

    def test_is_aligner_loaded(self):
        """Test aligner loaded state"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        assert not engine.is_aligner_loaded()

        engine._aligner_loaded = True
        assert engine.is_aligner_loaded()

    def test_get_stats(self):
        """Test statistics"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        stats = engine.get_stats()
        assert "total_requests" in stats
        assert "failed_requests" in stats
        assert "active_connections" in stats
        assert stats["total_requests"] == 0
        assert stats["failed_requests"] == 0

    def test_connection_management(self):
        """Test connection counter"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        assert engine._active_connections == 0

        engine.increment_connections()
        assert engine._active_connections == 1

        engine.increment_connections()
        assert engine._active_connections == 2

        engine.decrement_connections()
        assert engine._active_connections == 1

        # Test that it doesn't go negative
        engine.decrement_connections()
        engine.decrement_connections()
        assert engine._active_connections == 0

    @pytest.mark.asyncio
    async def test_load_returns_true_when_already_loaded(self):
        """Test that load returns quickly if already loaded"""
        from services.stt_server import STTEngine

        engine = STTEngine()
        engine._is_loaded = True

        result = await engine.load()
        assert result is True

    @pytest.mark.asyncio
    async def test_transcribe_raises_when_load_fails(self):
        """Test that transcribe raises error when model load fails"""
        from services.stt_server import STTEngine

        engine = STTEngine()

        with pytest.raises(RuntimeError, match="Failed to load STT model"):
            await engine.transcribe(b"fake audio")


class TestStructuredLogging:
    """Test structured logging"""

    def test_log_formatter(self):
        """Test StructuredLogFormatter"""
        import logging
        from services.stt_server import StructuredLogFormatter
        import json

        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert "timestamp" in data
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["message"] == "Test message"


class TestModels:
    """Test model classes"""

    def test_model_info(self):
        """Test ModelInfo model"""
        from services.stt_server import ModelInfo

        info = ModelInfo(
            name="qwen_asr",
            description="Test model",
            is_loaded=True,
            is_default=True,
        )
        assert info.name == "qwen_asr"
        assert info.is_loaded is True

    def test_health_status(self):
        """Test HealthStatus model"""
        from services.stt_server import HealthStatus

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

    def test_error_response(self):
        """Test ErrorResponse model"""
        from services.stt_server import ErrorResponse

        error = ErrorResponse(
            error_code="E5001",
            error_message="Test error",
            request_id="test-123",
        )
        assert error.error_code == "E5001"
        assert error.request_id == "test-123"


class TestTranscriptionRequest:
    """Test TranscriptionRequest model"""

    def test_defaults(self):
        """Test default values"""
        from services.stt_server import TranscriptionRequest

        req = TranscriptionRequest()
        assert req.language == "auto"
        assert req.return_timestamps is False

    def test_custom_values(self):
        """Test custom values"""
        from services.stt_server import TranscriptionRequest

        req = TranscriptionRequest(language="zh", return_timestamps=True)
        assert req.language == "zh"
        assert req.return_timestamps is True


# Integration tests (require actual model loading)
@pytest.mark.integration
class TestSTTEngineIntegration:
    """Integration tests for STTEngine"""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires model download")
    async def test_actual_load(self):
        """Test actual model loading (requires download)"""
        from services.stt_engine import STTEngine

        engine = STTEngine()
        result = await engine.load()
        assert result is True
        assert engine.is_model_loaded()
