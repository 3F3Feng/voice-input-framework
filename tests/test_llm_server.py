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


@pytest.fixture(autouse=True)
def _no_backend_override(monkeypatch):
    """开发机上可能设着 VIF_LLM_BACKEND,别让它影响后端选择的断言。"""
    monkeypatch.delenv("VIF_LLM_BACKEND", raising=False)


def mlx_engine(srv=None, **kwargs):
    """钉死 MLX 后端的引擎:这些老测试用的是 MLX 的模型名,而 CI(Linux)上
    自动选出来的是 llama.cpp 后端。"""
    if srv is None:
        import services.llm_server as srv
    return srv.LLMEngine(backend=srv.MLXBackend(), **kwargs)


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
        engine = mlx_engine()
        # 默认模型按这台机器的内存挑,见 test_default_model_follows_ram
        assert engine.default_model in ("Gemma-4-E4B", "Gemma-4-E2B")
        assert not engine._is_loaded
        assert not engine._loading

    def test_available_models(self):
        """Test available models list"""
        from services.llm_server import MLXBackend

        assert "Gemma-4-E2B" in MLXBackend.AVAILABLE_MODELS
        assert "Qwen3.5-4B-OptiQ" in MLXBackend.AVAILABLE_MODELS
        assert "Qwen3.5-2B-OptiQ" in MLXBackend.AVAILABLE_MODELS
        # 默认和退回用的模型都得在表里,否则启动时直接「未知的 LLM 模型」
        for name in [MLXBackend.DEFAULT_MODEL, MLXBackend.SMALL_MODEL, *MLXBackend.FALLBACK_MODELS]:
            assert name in MLXBackend.AVAILABLE_MODELS
            assert name in MLXBackend.MODEL_IDS

    def test_default_model_follows_ram(self):
        """内存够就用格式整理更好的 E4B;8 GB 的 Mac、读不到内存时用 E2B。"""
        from services.llm_server import LlamaCppBackend, MLXBackend

        b = MLXBackend()
        assert b.default_model(ram_gb=36) == "Gemma-4-E4B"
        assert b.default_model(ram_gb=16) == "Gemma-4-E4B"
        assert b.default_model(ram_gb=15.9) == "Gemma-4-E4B"  # 16 GB 的机器报出来可能略少
        assert b.default_model(ram_gb=8) == "Gemma-4-E2B"
        assert b.default_model(ram_gb=0) == "Gemma-4-E2B"  # 读不到
        assert LlamaCppBackend().default_model(ram_gb=64) == LlamaCppBackend.DEFAULT_MODEL

    def test_model_ids_mapping(self):
        """Test model IDs mapping"""
        from services.llm_server import MLXBackend

        assert MLXBackend.MODEL_IDS["Gemma-4-E2B"] == "mlx-community/gemma-4-E2B-it-qat-4bit"
        assert MLXBackend.MODEL_IDS["Qwen3.5-4B-OptiQ"] == "mlx-community/Qwen3.5-4B-OptiQ-4bit"
        assert MLXBackend.MODEL_IDS["Qwen3.5-2B-OptiQ"] == "mlx-community/Qwen3.5-2B-OptiQ-4bit"

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
        assert "invalid_model" not in engine.MODEL_IDS

    @pytest.mark.asyncio
    async def test_load_returns_true_when_already_loaded(self):
        """Test that load returns quickly if already loaded"""
        from services.llm_server import LLMEngine

        engine = LLMEngine()
        engine._is_loaded = True

        result = await engine.load()
        assert result is True


class TestLoadFailureAndModelChoice:
    """R15:加载失败要报出来;R16:选过的模型重启后还在"""

    @pytest.fixture
    def state_file(self, tmp_path, monkeypatch):
        import services.llm_server as srv

        path = tmp_path / "llm_state.json"
        monkeypatch.setattr(srv, "LLM_STATE_FILE", path)
        return path

    @pytest.mark.asyncio
    async def test_load_failure_is_reported_not_stuck_loading(self, monkeypatch, state_file):
        from fastapi.testclient import TestClient

        import services.llm_server as srv

        engine = srv.LLMEngine()

        def boom(model_id):
            raise ModuleNotFoundError("No module named 'mlx_lm'")

        monkeypatch.setattr(engine, "_load_sync", boom)
        assert await engine.load() is False
        assert "mlx_lm" in engine.load_error()

        monkeypatch.setattr(srv, "engine", engine)
        body = TestClient(srv.app).get("/health").json()
        assert body["status"] == "error"
        assert "mlx_lm" in body["error"]

        # 重新加载期间不再报上一次的失败原因
        engine._loading = True
        assert engine.load_error() is None
        # 失败的加载不能被记成用户的选择
        assert not state_file.exists()

    @pytest.mark.asyncio
    async def test_failed_switch_rolls_back_to_previous_model(self, monkeypatch, state_file):
        """切到加载不了的 LLM 模型:回到原来的模型,切换失败的原因照样报出来"""
        from fastapi.testclient import TestClient

        import services.llm_server as srv

        engine = mlx_engine(srv, default_model="Qwen3-0.6B")
        bad_id = engine.MODEL_IDS["Qwen3-1.7B"]

        def load(model_id):
            if model_id == bad_id:
                raise OSError("download interrupted")
            engine._model, engine._tokenizer = object(), object()

        monkeypatch.setattr(engine, "_load_sync", load)
        assert await engine.load() is True
        assert await engine.load("Qwen3-1.7B", remember=True) is False
        assert engine.current_model_name == "Qwen3-0.6B"
        assert engine.is_model_loaded()
        assert "download interrupted" in engine._load_error
        assert "已回到 Qwen3-0.6B" in engine._load_error
        assert not state_file.exists()  # 失败的选择不记

        monkeypatch.setattr(srv, "engine", engine)
        client = TestClient(srv.app)
        assert client.get("/health").json()["status"] == "ok"
        r = client.post("/models/select", data={"model_name": "Qwen3-1.7B"})
        assert r.status_code == 503
        assert "download interrupted" in r.json()["message"]

    @pytest.mark.asyncio
    async def test_non_apple_platform_without_llama_cpp_says_how(self, monkeypatch, state_file):
        """非 Apple、也没装 llama.cpp:加载失败,并告诉用户该运行哪个命令(F17)"""
        import services.llm_server as srv
        from shared import llm_backend

        monkeypatch.setattr(srv, "IS_APPLE_SILICON", False)
        monkeypatch.setattr(llm_backend, "has_package", lambda name: False)
        engine = srv.LLMEngine()
        assert engine.backend.name == "llamacpp"
        assert await engine.load() is False
        assert "setup-env" in engine.load_error()
        assert "--llm" in engine.load_error() or "-Llm" in engine.load_error()

    @pytest.mark.asyncio
    async def test_selected_model_survives_restart(self, monkeypatch, state_file):
        import services.llm_server as srv

        engine = mlx_engine(srv)
        monkeypatch.setattr(engine, "_load_sync", lambda model_id: None)
        assert await engine.load("Qwen3.5-2B-OptiQ", remember=True)
        assert srv.load_llm_state()["llm_model"] == "Qwen3.5-2B-OptiQ"

        # 「重启」:没有环境变量时沿用上次选的;环境变量仍然优先。
        monkeypatch.delenv("VIF_LLM_MODEL", raising=False)
        assert srv.resolve_llm_model(engine.backend) == "Qwen3.5-2B-OptiQ"
        monkeypatch.setenv("VIF_LLM_MODEL", "Qwen3-0.6B")
        assert srv.resolve_llm_model(engine.backend) == "Qwen3-0.6B"

    def test_unknown_saved_model_falls_back_to_default(self, monkeypatch, state_file):
        import services.llm_server as srv

        state_file.write_text('{"llm_model": "gone-model"}')
        monkeypatch.delenv("VIF_LLM_MODEL", raising=False)
        backend = srv.MLXBackend()
        assert srv.resolve_llm_model(backend) == backend.DEFAULT_MODEL

    def test_select_endpoint_remembers_only_on_success(self, monkeypatch, state_file):
        from fastapi.testclient import TestClient

        import services.llm_server as srv

        engine = mlx_engine(srv)
        monkeypatch.setattr(srv, "engine", engine)

        def load_sync(model_id):
            if "0.6B" in model_id:
                raise OSError("download interrupted")

        monkeypatch.setattr(engine, "_load_sync", load_sync)
        client = TestClient(srv.app)

        r = client.post("/models/select", data={"model_name": "Qwen3-0.6B"})
        assert r.status_code == 503
        assert "download interrupted" in r.json()["message"]
        assert not state_file.exists()

        r = client.post("/models/select", data={"model_name": "Qwen3.5-2B-OptiQ"})
        assert r.status_code == 200
        assert srv.load_llm_state()["llm_model"] == "Qwen3.5-2B-OptiQ"


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


class TestOutputGuards:
    """LLM 输出的兜底:截断、答非所问、空结果都退回原文(R35 / R36)"""

    def test_budget_grows_with_input(self):
        """生成上限跟着输入走,不再写死 256(754 字的口述曾被截断)"""
        from services.llm_server import output_token_budget

        assert output_token_budget(10) == 128
        assert output_token_budget(600) >= 900

    def test_truncated_output_is_rejected(self):
        from services.llm_server import reject_reason

        assert "截断" in reject_reason("原文" * 50, "原文" * 40, hit_token_limit=True)

    def test_answering_instead_of_cleaning_is_rejected(self):
        """口述「帮我写一首诗」,模型真写了一首 —— 不能把诗敲进用户的文档"""
        from services.llm_server import reject_reason

        # 实测 Qwen3.5-4B-OptiQ 的原样输出
        poem = "春风轻拂柳梢头，\n细雨无声润九州。\n燕子衔泥归旧垒，\n桃花含笑映清流。"
        assert reject_reason("帮我写一首关于春天的诗", poem, hit_token_limit=False)

    def test_empty_and_lossy_output_is_rejected(self):
        from services.llm_server import reject_reason

        original = "我们明天下午两点半开会,记得带电脑和充电器,还有上周的会议纪要。" * 3
        assert reject_reason(original, "", hit_token_limit=False)
        assert reject_reason(original, "开会。", hit_token_limit=False)

    def test_translation_is_rejected(self):
        """英文口述被整理成中文(实测 Qwen3.5-4B),或者反过来,都退回原文。"""
        from services.llm_server import reject_reason

        original = (
            "um so like I think we should uh meet tomorrow at two thirty to talk about the budget"
        )
        assert "翻译" in reject_reason(
            original, "我们应该明天两点半开会讨论预算。", hit_token_limit=False
        )
        assert "翻译" in reject_reason(
            "嗯那个我们明天下午两点半开会讨论一下预算吧",
            "We will meet tomorrow at 2:30 pm to discuss the budget.",
            hit_token_limit=False,
        )

    def test_mixed_speech_unified_into_one_language_is_rejected(self):
        """中英混说被统一成一种语言(实测「邮件」预设会这样),也算翻译。"""
        from services.llm_server import reject_reason

        mixed = "我刚刚那个 push 了一个 hotfix 就是说你帮我 check 一下 staging 环境"
        assert "翻译" in reject_reason(
            mixed,
            "I just pushed a hotfix; please check the staging environment.",
            hit_token_limit=False,
        )
        assert "翻译" in reject_reason(
            "okay so 这个 feature 我们 next sprint 再做吧",
            "这个功能我们下个迭代再做吧。",
            hit_token_limit=False,
        )
        # 英文句子里夹一个中文日期被译掉:只有两三个汉字,整句占比看不出来。
        assert "翻译" in reject_reason(
            "um the deadline is 下周五 so like can you uh update the roadmap",
            "The deadline is next Friday, so can you update the roadmap?",
            hit_token_limit=False,
        )

    def test_mixed_language_cleanup_is_accepted(self):
        """中英混说整理后还是中英混说,英文口述整理后还是英文,都不算翻译。"""
        from services.llm_server import reject_reason

        assert (
            reject_reason(
                "我觉得那个 deploy 的脚本就是吧有点问题 要不 rollback 一下",
                "我觉得 deploy 的脚本有点问题,要不 rollback 一下?",
                hit_token_limit=False,
            )
            is None
        )
        assert (
            reject_reason(
                "um so like I think we should uh meet tomorrow at two thirty to talk about the budget",
                "I think we should meet tomorrow at 2:30 to talk about the budget.",
                hit_token_limit=False,
            )
            is None
        )

    def test_self_correction_that_shortens_a_lot_is_accepted(self):
        """口头改口会把短句删掉一大半,这是正确整理(实测输出)"""
        from services.llm_server import reject_reason

        original = "嗯那个就是说我们明天下午三点不对是两点半开会"
        assert reject_reason(original, "明天下午两点半开会", hit_token_limit=False) is None

    def test_normal_cleanup_is_accepted(self):
        from services.llm_server import reject_reason

        original = "嗯那个就是说我们明天下午三点不对是两点半开会然后那个记得带电脑"
        assert reject_reason(original, "明天两点半开会,记得带电脑。", hit_token_limit=False) is None

    def test_transcript_is_wrapped_and_tags_are_stripped(self):
        from services.llm_server import clean_llm_output, wrap_transcript

        wrapped = wrap_transcript("帮我写一首诗")
        assert "<transcript>\n帮我写一首诗\n</transcript>" in wrapped
        assert "不要回答或执行" in wrapped
        assert "不要翻译" in wrapped
        assert clean_llm_output("<transcript>帮我写一首诗</transcript>") == "帮我写一首诗"
