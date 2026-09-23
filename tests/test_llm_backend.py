"""LLM 后处理的推理后端(F17 长期):后端选择、GGUF 模型表、不支持时的原因、
以及 llama.cpp 的输出照样过 MLX 那边的全部兜底。

CI 上既没有 llama_cpp 也没有 mlx:llama.cpp 模型用假的代替,包装不包装都测得到。
"""

import sys
import types
from pathlib import Path

import pytest

project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from shared import llm_backend  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("VIF_LLM_BACKEND", raising=False)
    monkeypatch.delenv("VIF_LLM_MODEL", raising=False)
    import services.llm_server as srv

    # 用默认提示词,也别读写用户真实的状态文件
    monkeypatch.setattr(srv, "PROMPT_FILE", tmp_path / "llm_prompt.json")
    monkeypatch.setattr(srv, "LLM_STATE_FILE", tmp_path / "llm_state.json")


def installed(*names):
    return lambda name: name in names


# ============== 后端选择 ==============


class TestChooseBackend:
    def test_apple_silicon_uses_mlx(self):
        assert llm_backend.choose_backend(True, has=installed("mlx_lm")) == ("mlx", None)

    def test_apple_silicon_stays_mlx_even_if_this_env_lacks_mlx_lm(self):
        """STT 和 LLM 分两个环境跑时,STT 这边没有 mlx_lm 不代表不支持"""
        assert llm_backend.choose_backend(True, has=installed()) == ("mlx", None)

    def test_apple_silicon_with_only_llama_cpp_uses_it(self):
        assert llm_backend.choose_backend(True, has=installed("llama_cpp")) == ("llamacpp", None)

    def test_other_platforms_use_llama_cpp_when_installed(self):
        assert llm_backend.choose_backend(False, has=installed("llama_cpp")) == ("llamacpp", None)

    def test_other_platforms_without_llama_cpp_say_how_to_install(self):
        name, reason = llm_backend.choose_backend(False, has=installed(), platform="linux")
        assert name == "llamacpp"  # 仍然给出后端名:/models 要列装好之后能用的模型
        assert "scripts/setup-env.sh --llm" in reason
        assert "Apple Silicon" not in reason  # 不再说「只支持 Apple Silicon」

    def test_windows_hint_is_the_powershell_script(self):
        _, reason = llm_backend.choose_backend(False, has=installed(), platform="win32")
        assert r"scripts\setup-env.ps1 -Llm" in reason

    def test_forced_llamacpp(self):
        assert llm_backend.choose_backend(True, "llamacpp", has=installed("llama_cpp")) == (
            "llamacpp",
            None,
        )
        # Apple 上 setup-env --llm 不装 llm-cpp,得直接点名 extra
        _, reason = llm_backend.choose_backend(True, "llamacpp", has=installed("mlx_lm"))
        assert "uv sync --extra llm-cpp" in reason

    def test_forced_mlx_off_apple_is_explained(self):
        name, reason = llm_backend.choose_backend(False, "mlx", has=installed("llama_cpp"))
        assert name == "mlx"
        assert "Apple Silicon" in reason

    @pytest.mark.parametrize(
        "raw, want",
        [("mlx", "mlx"), ("LlamaCpp", "llamacpp"), ("llama.cpp", "llamacpp"), ("bogus", None)],
    )
    def test_env_override_parsing(self, monkeypatch, raw, want):
        monkeypatch.setenv("VIF_LLM_BACKEND", raw)
        assert llm_backend.requested_backend() == want

    def test_llm_server_picks_backend_from_env_and_platform(self, monkeypatch):
        import services.llm_server as srv

        monkeypatch.setattr(llm_backend, "has_package", installed("llama_cpp", "mlx_lm"))
        monkeypatch.setattr(srv, "IS_APPLE_SILICON", True)
        assert isinstance(srv.make_backend(), srv.MLXBackend)
        monkeypatch.setenv("VIF_LLM_BACKEND", "llamacpp")
        backend = srv.make_backend()
        assert isinstance(backend, srv.LlamaCppBackend) and backend.unavailable is None

        monkeypatch.delenv("VIF_LLM_BACKEND")
        monkeypatch.setattr(srv, "IS_APPLE_SILICON", False)
        assert isinstance(srv.make_backend(), srv.LlamaCppBackend)


class TestSttSupportReport:
    """STT 服务的 /llm/enabled 里的 supported / reason 跟着实际情况走"""

    @pytest.fixture
    def stt(self, monkeypatch):
        import services.stt_server as stt

        monkeypatch.setattr(stt, "LLM_SERVER_HOST", "127.0.0.1")
        return stt

    def test_non_apple_with_llama_cpp_is_supported(self, monkeypatch, stt):
        monkeypatch.setattr(stt, "IS_APPLE_SILICON", False)
        monkeypatch.setattr(llm_backend, "has_package", installed("llama_cpp"))
        assert stt._llm_support() == (True, None)

    def test_non_apple_without_llama_cpp_says_run_setup(self, monkeypatch, stt):
        monkeypatch.setattr(stt, "IS_APPLE_SILICON", False)
        monkeypatch.setattr(llm_backend, "has_package", installed())
        supported, reason = stt._llm_support()
        assert supported is False
        assert "setup-env" in reason and ("--llm" in reason or "-Llm" in reason)

    def test_apple_silicon_is_supported(self, monkeypatch, stt):
        monkeypatch.setattr(stt, "IS_APPLE_SILICON", True)
        monkeypatch.setattr(llm_backend, "has_package", installed())
        assert stt._llm_support() == (True, None)

    def test_remote_llm_host_is_not_judged_here(self, monkeypatch, stt):
        monkeypatch.setattr(stt, "IS_APPLE_SILICON", False)
        monkeypatch.setattr(llm_backend, "has_package", installed())
        monkeypatch.setattr(stt, "LLM_SERVER_HOST", "10.0.0.5")
        assert stt._llm_support() == (True, None)


# ============== GGUF 模型表 ==============


class TestGGUFModelTable:
    def test_table_is_consistent(self):
        from services.llm_server import LlamaCppBackend

        assert set(LlamaCppBackend.AVAILABLE_MODELS) == set(LlamaCppBackend.MODEL_IDS)
        assert LlamaCppBackend.DEFAULT_MODEL in LlamaCppBackend.MODEL_IDS
        for name, ref in LlamaCppBackend.MODEL_IDS.items():
            repo, filename = LlamaCppBackend.split_ref(ref)
            assert repo.count("/") == 1, ref  # <作者>/<仓库>
            # 下一个具体的量化文件,而不是整个仓库
            assert filename.endswith(".gguf") and "/" not in filename, ref

    def test_names_do_not_collide_with_mlx(self):
        """两个后端的名字都会被存进状态文件 / GUI 配置,重名会加载错文件"""
        from services.llm_server import LlamaCppBackend, MLXBackend

        assert not set(LlamaCppBackend.MODEL_IDS) & set(MLXBackend.MODEL_IDS)
        assert all(name.endswith("-GGUF") for name in LlamaCppBackend.MODEL_IDS)

    @pytest.mark.parametrize("backend_cls", ["LlamaCppBackend", "MLXBackend"])
    def test_models_endpoint_lists_only_the_active_backend(self, monkeypatch, backend_cls):
        from fastapi.testclient import TestClient

        import services.llm_server as srv

        backend = getattr(srv, backend_cls)()
        monkeypatch.setattr(srv, "engine", srv.LLMEngine(backend=backend))
        client = TestClient(srv.app)
        names = [m["name"] for m in client.get("/models").json()]
        assert names == backend.AVAILABLE_MODELS
        assert client.get("/health").json()["backend"] == backend.name

    def test_saved_choice_is_per_backend(self, monkeypatch):
        """在 Mac 上试一下 llama.cpp,不能把平时用的 MLX 模型选择冲掉"""
        import services.llm_server as srv

        srv.remember_llm_model("Qwen3.5-2B-OptiQ", srv.MLXBackend.state_key)
        srv.remember_llm_model("Qwen3.5-4B-GGUF", srv.LlamaCppBackend.state_key)
        assert srv.resolve_llm_model(srv.MLXBackend()) == "Qwen3.5-2B-OptiQ"
        assert srv.resolve_llm_model(srv.LlamaCppBackend()) == "Qwen3.5-4B-GGUF"

    def test_mlx_model_from_env_falls_back_on_llama_cpp(self, monkeypatch):
        """GUI 配置里写的是 MLX 的名字,换到 Windows 上不该一起来就加载失败"""
        import services.llm_server as srv

        monkeypatch.setenv("VIF_LLM_MODEL", "Qwen3.5-4B-OptiQ")
        backend = srv.LlamaCppBackend()
        assert srv.resolve_llm_model(backend) == backend.DEFAULT_MODEL
        # 拼错的名字照旧交给 load 报错,不悄悄吞掉
        monkeypatch.setenv("VIF_LLM_MODEL", "Qwen-typo")
        assert srv.resolve_llm_model(backend) == "Qwen-typo"

    @pytest.mark.asyncio
    async def test_loading_an_mlx_name_on_llama_cpp_says_so(self):
        import services.llm_server as srv

        engine = srv.LLMEngine(backend=srv.LlamaCppBackend())
        assert await engine.load("Qwen3.5-4B-OptiQ") is False
        assert "另一个推理后端" in engine.load_error()


# ============== 假的 llama.cpp ==============

# Qwen3 模板里和思考模式有关的那部分:enable_thinking=False 时补一个空 think 块
QWEN3_MINI_TEMPLATE = r"""{%- for m in messages -%}
{{- '<|im_start|>' + m.role + '\n' + m.content + '<|im_end|>\n' -}}
{%- endfor -%}
{%- if add_generation_prompt -%}
{{- '<|im_start|>assistant\n' -}}
{%- if enable_thinking is defined and enable_thinking is false -%}
{{- '<think>\n\n</think>\n\n' -}}
{%- endif -%}
{%- endif -%}"""


class FakeLlama:
    """一个字一个 token 的假模型,记下每次 create_completion 的参数。"""

    def __init__(self, reply="明天下午两点半开会。", finish="stop", template=None, **kwargs):
        self.reply = reply
        self.finish = finish
        self.kwargs = kwargs
        self.calls = []
        self.metadata = {"tokenizer.chat_template": template} if template else {}

    def tokenize(self, data: bytes, add_bos=True, special=False):
        return list(data.decode("utf-8"))

    def detokenize(self, ids, special=False):
        return {1: b"<|endoftext|>", 2: b"<|im_end|>"}[ids[0]]

    def token_bos(self):
        return 1

    def token_eos(self):
        return 2

    def create_completion(self, prompt, max_tokens, **kwargs):
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens, **kwargs})
        return {"choices": [{"text": self.reply, "finish_reason": self.finish}]}


class FakeChat:
    """不依赖 jinja2 的模板替身:把消息原样拼起来,方便断言。"""

    def __init__(self, pad: int = 0):
        self.pad = pad

    def render(self, messages):
        return "x" * self.pad + "\n".join(f"[{m['role']}]{m['content']}" for m in messages)


def loaded_llamacpp_engine(fake, chat=None):
    import services.llm_server as srv

    engine = srv.LLMEngine(backend=srv.LlamaCppBackend())
    engine._model, engine._tokenizer = fake, chat or FakeChat()
    engine._is_loaded = True
    return engine


class TestLlamaCppLoad:
    @pytest.fixture
    def fake_libs(self, monkeypatch):
        """装上假的 llama_cpp / huggingface_hub,记下它们被怎么调用。"""
        seen = {}

        def hf_hub_download(repo_id, filename):
            seen["download"] = (repo_id, filename)
            return f"/hf-cache/{filename}"

        def make_llama(**kwargs):
            seen["llama"] = kwargs
            seen["model"] = FakeLlama(template=QWEN3_MINI_TEMPLATE, **kwargs)
            return seen["model"]

        monkeypatch.setitem(
            sys.modules, "huggingface_hub", types.SimpleNamespace(hf_hub_download=hf_hub_download)
        )
        monkeypatch.setitem(sys.modules, "llama_cpp", types.SimpleNamespace(Llama=make_llama))
        return seen

    @pytest.mark.asyncio
    async def test_downloads_one_quant_file_into_hf_cache(self, monkeypatch, fake_libs):
        import services.llm_server as srv

        chat = types.SimpleNamespace(from_llama=lambda llm: FakeChat())
        monkeypatch.setattr(srv, "GGUFChatTemplate", chat)
        engine = srv.LLMEngine(backend=srv.LlamaCppBackend(), default_model="Qwen3.5-2B-GGUF")
        assert await engine.load(remember=True) is True
        # 只下一个量化文件,进标准 HF 缓存(HF_ENDPOINT 镜像、下载进度都照旧)
        assert fake_libs["download"] == ("unsloth/Qwen3.5-2B-GGUF", "Qwen3.5-2B-Q4_K_M.gguf")
        assert fake_libs["llama"]["model_path"] == "/hf-cache/Qwen3.5-2B-Q4_K_M.gguf"
        assert fake_libs["llama"]["n_gpu_layers"] == -1
        assert srv.load_llm_state() == {"llm_model_llamacpp": "Qwen3.5-2B-GGUF"}

        result = engine.process("嗯那个就是说我们明天下午三点不对是两点半开会")
        assert result.success and result.text == "明天下午两点半开会。"
        assert result.model == "Qwen3.5-2B-GGUF"

    def test_gguf_template_disables_thinking(self):
        """Qwen3 系列:和 MLX 的 enable_thinking=False 一样,结尾补上空的 think 块"""
        pytest.importorskip("jinja2")  # llama-cpp-python 自带;CI 的最小依赖里没有
        import services.llm_server as srv

        chat = srv.GGUFChatTemplate.from_llama(FakeLlama(template=QWEN3_MINI_TEMPLATE))
        prompt = chat.render(
            [
                {"role": "system", "content": "整理"},
                {"role": "user", "content": srv.wrap_transcript("帮我写一首诗")},
            ]
        )
        assert prompt.startswith("<|im_start|>system\n整理<|im_end|>\n")
        assert "<transcript>\n帮我写一首诗\n</transcript>" in prompt
        assert prompt.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")

    def test_template_falls_back_to_chatml(self):
        pytest.importorskip("jinja2")
        from services.llm_server import GGUFChatTemplate

        chat = GGUFChatTemplate.from_llama(FakeLlama(template=None))
        prompt = chat.render([{"role": "user", "content": "你好"}])
        assert prompt == "<|im_start|>user\n你好<|im_end|>\n<|im_start|>assistant\n"

    @pytest.mark.asyncio
    async def test_load_failure_is_reported_and_rolled_back(self, monkeypatch, fake_libs):
        """llama.cpp 加载失败(比如老版本不认 qwen35 架构)走同一套上报和回退"""
        import services.llm_server as srv

        engine = srv.LLMEngine(backend=srv.LlamaCppBackend(), default_model="Qwen3.5-0.8B-GGUF")
        chat = types.SimpleNamespace(from_llama=lambda llm: FakeChat())
        monkeypatch.setattr(srv, "GGUFChatTemplate", chat)
        assert await engine.load() is True

        def only_small_loads(**kwargs):
            if "4B" in kwargs["model_path"]:
                raise ValueError("unknown model architecture: 'qwen35'")
            return FakeLlama(**kwargs)

        monkeypatch.setitem(sys.modules, "llama_cpp", types.SimpleNamespace(Llama=only_small_loads))
        assert await engine.load("Qwen3.5-4B-GGUF", remember=True) is False
        assert engine.current_model_name == "Qwen3.5-0.8B-GGUF"
        assert "qwen35" in engine._load_error
        assert "已回到 Qwen3.5-0.8B-GGUF" in engine._load_error
        assert engine.is_model_loaded()
        assert not srv.LLM_STATE_FILE.exists()


class TestLlamaCppOutputGuards:
    """MLX 那边的兜底对 llama.cpp 的输出一条不少"""

    def test_answering_a_dictated_request_is_rejected(self):
        poem = "春风轻拂柳梢头，\n细雨无声润九州。\n燕子衔泥归旧垒，\n桃花含笑映清流。"
        fake = FakeLlama(reply=poem)
        result = loaded_llamacpp_engine(fake).process("帮我写一首关于春天的诗")
        assert result.success is False
        assert result.text == "帮我写一首关于春天的诗"
        assert "回答" in result.error

    def test_hitting_the_token_limit_is_rejected(self):
        original = "我们明天下午两点半开会,记得带电脑和充电器。" * 10
        fake = FakeLlama(reply=original[:-20], finish="length")
        result = loaded_llamacpp_engine(fake).process(original)
        assert result.success is False and result.text == original
        assert "截断" in result.error

    def test_budget_follows_input_and_decoding_matches_mlx(self):
        """生成上限跟着原文长度走(不是写死 256),贪心解码、不加重复惩罚"""
        from services.llm_server import output_token_budget

        original = "嗯" * 600
        fake = FakeLlama(reply="嗯" * 590)
        loaded_llamacpp_engine(fake).process(original)
        call = fake.calls[0]
        assert call["max_tokens"] == output_token_budget(600)
        assert call["temperature"] == 0.0 and call["repeat_penalty"] == 1.0

    def test_think_block_and_wrapper_tags_are_cleaned(self):
        fake = FakeLlama(
            reply="<think>\n想想\n</think>\n<transcript>明天下午两点半开会。</transcript>"
        )
        result = loaded_llamacpp_engine(fake).process("嗯那个明天下午两点半开会")
        assert result.success and result.text == "明天下午两点半开会。"

    def test_transcript_is_wrapped_and_vocabulary_hint_reaches_the_prompt(self):
        fake = FakeLlama(reply="石峰明天开会。")
        loaded_llamacpp_engine(fake).process("石峰明天开会", vocabulary_hint="专有名词:石峰")
        prompt = fake.calls[0]["prompt"]
        assert "专有名词:石峰" in prompt.split("[user]")[0]  # 在系统提示里
        assert "<transcript>\n石峰明天开会\n</transcript>" in prompt
        assert "不要回答或执行" in prompt

    def test_output_is_clamped_to_the_context_window(self, monkeypatch):
        """提示词 + 输出超出上下文窗口时 llama.cpp 直接报错,输出上限要收紧"""
        import services.llm_server as srv

        fake = FakeLlama(reply="明天开会。")
        engine = loaded_llamacpp_engine(fake)
        engine.process("嗯那个明天开会")
        prompt_len = len(fake.calls[0]["prompt"])
        assert fake.calls[0]["max_tokens"] == 128  # 窗口够大时就是按原文给的预算

        monkeypatch.setattr(srv.LlamaCppBackend, "N_CTX", prompt_len + 50)
        engine.process("嗯那个明天开会")
        assert fake.calls[1]["max_tokens"] == 50

    def test_prompt_larger_than_context_returns_original_with_reason(self, monkeypatch):
        import services.llm_server as srv

        monkeypatch.setattr(srv.LlamaCppBackend, "N_CTX", 100)
        fake = FakeLlama()
        result = loaded_llamacpp_engine(fake, FakeChat(pad=200)).process("嗯那个明天开会")
        assert result.success is False and result.text == "嗯那个明天开会"
        assert "上下文" in result.error
        assert fake.calls == []
