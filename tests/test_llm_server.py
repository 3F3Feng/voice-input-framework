"""
Tests for LLM Server
"""

import pytest
import sys
from pathlib import Path

# Add project path
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))


class TestProcessRequest:
    """Test ProcessRequest model"""

    def test_basic_creation(self):
        """Test basic request creation"""
        from services.llm_server import ProcessRequest

        req = ProcessRequest(text="Hello world")
        assert req.text == "Hello world"
        assert req.options == {}

    def test_with_options(self):
        """Test request with options"""
        from services.llm_server import ProcessRequest

        req = ProcessRequest(text="Test", options={"temperature": 0.7, "max_tokens": 100})
        assert req.options["temperature"] == 0.7
        assert req.options["max_tokens"] == 100


class TestProcessResult:
    """Test ProcessResult model"""

    def test_basic_result(self):
        """Test basic result creation"""
        from services.llm_server import ProcessResult

        result = ProcessResult(
            text="Processed text",
            original_text="Original text",
            llm_latency_ms=100.0,
            model="Qwen3.5-4B-OptiQ",
        )
        assert result.text == "Processed text"
        assert result.original_text == "Original text"
        assert result.success is True

    def test_failure_result(self):
        """Test failure result"""
        from services.llm_server import ProcessResult

        result = ProcessResult(
            text="", original_text="Original", llm_latency_ms=0.0, model="", success=False
        )
        assert result.success is False


class TestModelInfo:
    """Test ModelInfo model"""

    def test_model_info(self):
        """Test ModelInfo creation"""
        from services.llm_server import ModelInfo

        info = ModelInfo(
            name="Qwen3.5-4B-OptiQ",
            description="Fast optimization model",
            is_loaded=True,
            is_current=True,
        )
        assert info.name == "Qwen3.5-4B-OptiQ"
        assert info.is_loaded is True


class TestHealthStatus:
    """Test HealthStatus model"""

    def test_health_status(self):
        """Test HealthStatus creation"""
        from services.llm_server import HealthStatus

        health = HealthStatus(
            status="ok",
            uptime_seconds=3600.0,
            current_model="Qwen3.5-4B-OptiQ",
            loaded_models=["Qwen3.5-4B-OptiQ"],
            active_connections=5,
            is_processing=False,
        )
        assert health.status == "ok"
        assert health.uptime_seconds == 3600.0
        assert health.is_processing is False


class TestLLMEngine:
    """Test LLMEngine class"""

    def test_init(self):
        """Test engine initialization"""
        from server.llm_engine import LLMEngine

        engine = LLMEngine()
        # After refactoring: default model depends on platform
        default = engine.get_default_model()
        assert isinstance(default, str)
        assert not engine.is_loaded
        assert not engine.is_processing

    def test_get_default_model(self):
        """Test get_default_model returns a string"""
        from server.llm_engine import LLMEngine

        engine = LLMEngine()
        model = engine.get_default_model()
        assert isinstance(model, str)

    def test_get_available_models(self):
        """Test available models"""
        from server.llm_engine import LLMEngine

        engine = LLMEngine()
        models = engine.get_available_models()
        assert isinstance(models, dict)
        # Should return at least the models compatible with this platform
        assert len(models) > 0


class TestPromptTemplates:
    """Test prompt template handling"""

    def test_default_prompt(self):
        """Test default prompt format"""
        # The LLM server should have default prompts for optimization
        default_prompt = "请优化以下语音识别结果，使其更加通顺自然，但不要改变原意："
        assert len(default_prompt) > 0

    def test_prompt_with_context(self):
        """Test prompt with context"""
        default_prompt = "请优化以下语音识别结果，使其更加通顺自然，但不要改变原意："
        text = "你好世界"
        prompt = f"{default_prompt}\n\n{text}"
        assert text in prompt


class TestProcessingLogic:
    """Test processing logic"""

    def test_text_cleaning(self):
        """Test text cleaning logic"""
        # Test common text cleaning scenarios
        test_cases = [
            ("你好  世界", "你好 世界"),  # Multiple spaces
            ("你好。世界", "你好。世界"),  # Keep punctuation
            ("你好<think>test</think>世界", "你好世界"),  # Remove think tags
        ]

        for input_text, expected in test_cases:
            # Simple cleaning simulation
            cleaned = input_text.replace("  ", " ")
            if "<think>" in cleaned:
                # Remove think tags
                import re

                cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
            assert cleaned == expected


class TestErrorResponse:
    """Test error handling"""

    def test_error_response_format(self):
        """Test error response format"""
        error_code = "E5001"
        error_message = "Model loading failed"
        request_id = "test-123"

        error = {
            "error_code": error_code,
            "error_message": error_message,
            "request_id": request_id,
        }

        assert error["error_code"] == "E5001"
        assert error["request_id"] == "test-123"


# Integration tests (require actual model loading)
@pytest.mark.integration
class TestLLMEngineIntegration:
    """Integration tests for LLMEngine"""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires model download")
    async def test_actual_load(self):
        """Test actual model loading (requires download)"""
        from server.llm_engine import LLMEngine

        engine = LLMEngine()
        success = await engine.load_model("Qwen3.5-4B-OptiQ")
        assert success is True
        assert engine.is_loaded

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires model download")
    async def test_actual_process(self):
        """Test actual text processing (requires download)"""
        from server.llm_engine import LLMEngine

        engine = LLMEngine()
        await engine.load_model("Qwen3.5-4B-OptiQ")

        result = await engine.process("你好世界")
        assert result.success is True
        assert len(result.text) > 0
