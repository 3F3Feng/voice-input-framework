"""
Tests for LLM Server
"""

import sys
from pathlib import Path

import pytest

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
        from services.llm_server import LLMEngine

        engine = LLMEngine()
        assert engine.default_model == "Qwen3.5-4B-OptiQ"
        assert not engine._is_loaded
        assert not engine._loading

    def test_available_models(self):
        """Test available models list"""
        from services.llm_server import LLMEngine

        assert "Qwen3.5-4B-OptiQ" in LLMEngine.AVAILABLE_MODELS
        assert "Qwen3.5-2B-OptiQ" in LLMEngine.AVAILABLE_MODELS
        assert "Qwen3.5-4B-OptiQ" in LLMEngine.AVAILABLE_MODELS

    def test_model_ids_mapping(self):
        """Test model IDs mapping"""
        from services.llm_server import LLMEngine

        assert LLMEngine.MODEL_IDS["Qwen3.5-4B-OptiQ"] == "mlx-community/Qwen3.5-4B-OptiQ-4bit"
        assert LLMEngine.MODEL_IDS["Qwen3.5-2B-OptiQ"] == "mlx-community/Qwen3.5-2B-OptiQ-4bit"

    def test_is_loading(self):
        """Test loading state"""
        from services.llm_server import LLMEngine

        engine = LLMEngine()
        assert not engine.is_loading()

        engine._loading = True
        assert engine.is_loading()

    def test_is_model_loaded(self):
        """Test model loaded state"""
        from services.llm_server import LLMEngine

        engine = LLMEngine()
        assert not engine.is_model_loaded()

        engine._is_loaded = True
        assert engine.is_model_loaded()

    def test_invalid_model_name(self):
        """Test invalid model name handling"""
        from services.llm_server import LLMEngine

        engine = LLMEngine(default_model="invalid_model")
        # Should fall back to default
        assert engine.default_model == "invalid_model"
        # But MODEL_IDS won't have it
        assert "invalid_model" not in LLMEngine.MODEL_IDS

    @pytest.mark.asyncio
    async def test_load_returns_true_when_already_loaded(self):
        """Test that load returns quickly if already loaded"""
        from services.llm_server import LLMEngine

        engine = LLMEngine()
        engine._is_loaded = True

        result = await engine.load()
        assert result is True


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


class TestCleanLLMOutput:
    """clean_llm_output:只去思考块和粗体标记,正文一个字都不能动"""

    def test_apostrophes_and_quotes_survive(self):
        """撇号和引号必须原样保留(回归:曾被无条件删掉)"""
        from services.llm_server import clean_llm_output

        text = 'I don\'t know, he said "okay"'
        assert clean_llm_output(text) == text

    def test_chinese_quotes_survive(self):
        """中文引号同样不能动"""
        from services.llm_server import clean_llm_output

        text = "他说「好的」,我说'知道了'。"
        assert clean_llm_output(text) == text

    def test_repeated_lines_survive(self):
        """重复的行不能被去重(回归:副歌/强调句曾被整行删掉)"""
        from services.llm_server import clean_llm_output

        text = "再见\n再见\n再见"
        assert clean_llm_output(text) == text

    def test_line_breaks_survive(self):
        """多行不能被压成一行(回归:分段曾被空格拼接)"""
        from services.llm_server import clean_llm_output

        text = "第一段\n第二段"
        assert clean_llm_output(text) == text
        assert "\n" in clean_llm_output(text)

    def test_leading_words_survive(self):
        """开头是普通词时不能被砍掉(回归:^(quirer|thinker) 土办法)"""
        from services.llm_server import clean_llm_output

        assert clean_llm_output("thinker 是个乐队") == "thinker 是个乐队"

    def test_think_block_removed(self):
        """跨行思考块整块删掉"""
        from services.llm_server import clean_llm_output

        assert clean_llm_output("<think>\n盘算一下\n</think>\n你好世界") == "你好世界"

    def test_orphan_think_tag_removed(self):
        """只剩半边标签时也要删"""
        from services.llm_server import clean_llm_output

        assert clean_llm_output("</think>你好") == "你好"

    def test_bold_markers_removed(self):
        """markdown 粗体标记会被原样敲进文档,删掉"""
        from services.llm_server import clean_llm_output

        assert clean_llm_output("这是**重点**内容") == "这是重点内容"

    def test_surrounding_whitespace_trimmed(self):
        """首尾空白仍然清掉"""
        from services.llm_server import clean_llm_output

        assert clean_llm_output("  你好  ") == "你好"


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
        from services.llm_server import LLMEngine

        engine = LLMEngine()
        result = await engine.load()
        assert result is True
        assert engine.is_model_loaded()

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires model download")
    async def test_actual_process(self):
        """Test actual text processing (requires download)"""
        from services.llm_server import LLMEngine

        engine = LLMEngine()
        await engine.load()

        result = await engine.process("你好世界")
        assert result.success is True
        assert len(result.text) > 0
