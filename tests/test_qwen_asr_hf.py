"""Qwen3-ASR 的 transformers 引擎(services/qwen_asr_hf.py):非 Apple 平台的中文识别。

真模型在本机(M3 Max,CPU fp32 / MPS fp16)实测过;这里用假的 processor / model
钉住接线:语言怎么传、热词怎么传、生成长度给多少、模型报的语言名怎么换回代码。
"""

import numpy as np
import pytest

from services import model_catalog, qwen_asr_hf
from services.stt_engine import _infer_sync, resolve_language
from shared.model_registry import MODELS_CONFIG


class FakeBatch(dict):
    def to(self, device, dtype):
        self.moved_to = (device, dtype)
        return self


class FakeProcessor:
    def __init__(self, parsed):
        self.parsed = parsed
        self.requests = []

    def apply_transcription_request(self, **kwargs):
        self.requests.append(kwargs)
        return FakeBatch(input_ids=np.zeros((1, 7), dtype=np.int64))

    def decode(self, generated, return_format):
        assert return_format == "parsed"
        # 只解码新生成的那一段,不带提示词
        assert generated.shape == (1, 3)
        return [self.parsed]


class FakeModel:
    device = "cuda:0"
    dtype = "float16"

    def __init__(self):
        self.generate_kwargs = None

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return np.zeros((1, 10), dtype=np.int64)


class FakeTorch:
    class inference_mode:  # noqa: N801 - 模仿 torch.inference_mode()
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False


def make_engine(parsed):
    engine = object.__new__(qwen_asr_hf.QwenASRTransformers)
    engine._torch = FakeTorch
    engine.processor = FakeProcessor(parsed)
    engine.model = FakeModel()
    return engine


def test_auto_detect_passes_only_the_audio_and_reports_the_language():
    engine = make_engine({"language": "Chinese", "transcription": " 今天下午三点开会。 "})
    audio = np.zeros(16000 * 4, dtype=np.float32)
    text, spoken = engine.transcribe(audio, 16000, None)
    assert (text, spoken) == ("今天下午三点开会。", "Chinese")
    (request,) = engine.processor.requests
    assert set(request) == {"audio"}  # 没指定语言、没有热词:都不传
    assert engine.model.generate_kwargs["max_new_tokens"] == qwen_asr_hf.token_budget(4)


def test_forced_language_and_hotwords_are_forwarded():
    # 指定了语言时模型不再报语言
    engine = make_engine({"language": None, "transcription": "石枫在用 Tauri"})
    text, spoken = engine.transcribe(
        np.zeros(8000, dtype=np.float32), 16000, "Chinese", context="石枫 Tauri"
    )
    assert (text, spoken) == ("石枫在用 Tauri", None)
    (request,) = engine.processor.requests
    assert request["language"] == "Chinese"
    assert request["prompt"] == "石枫 Tauri"


def test_token_budget_grows_with_audio_and_is_capped():
    assert qwen_asr_hf.token_budget(0) == qwen_asr_hf.MIN_NEW_TOKENS
    assert qwen_asr_hf.token_budget(28) == qwen_asr_hf.MIN_NEW_TOKENS + 28 * 15
    assert qwen_asr_hf.token_budget(3600) == qwen_asr_hf.MAX_NEW_TOKENS


def test_cpu_always_runs_float32_and_gpus_keep_their_half_precision():
    assert qwen_asr_hf.pick_dtype_name("cpu", "bfloat16") == "float32"
    assert qwen_asr_hf.pick_dtype_name("cpu", "float32") == "float32"
    assert qwen_asr_hf.pick_dtype_name("cuda", "float16") == "float16"
    assert qwen_asr_hf.pick_dtype_name("mps", "float16") == "float16"


@pytest.mark.parametrize(
    ("requested", "spoken", "expected"),
    [
        (None, "Chinese", "zh"),  # 自动检测:模型报语言名,换回代码
        (None, "Cantonese", "yue"),
        ("Chinese", None, "zh"),  # 指定了语言:模型不报,用请求的那个
        (None, "Swahili", "Swahili"),  # 表里没有的语言名原样带回,不丢
        (None, None, None),
    ],
)
def test_infer_maps_language_names_back_to_codes(requested, spoken, expected):
    engine = make_engine({"language": spoken, "transcription": "ok"})
    text, lang = _infer_sync(
        engine, "qwen_asr_hf", np.zeros(1600, dtype=np.float32), 16000, requested
    )
    assert (text, lang) == ("ok", expected)


def test_client_language_codes_become_qwen_names_for_this_engine():
    assert resolve_language("zh", "qwen_asr_hf") == "Chinese"
    assert resolve_language("yue", "qwen_asr_hf") == "Cantonese"
    assert resolve_language("auto", "qwen_asr_hf") is None


def test_registry_models_use_the_transformers_checkpoints():
    for name in ("qwen_asr", "qwen_asr_small"):
        info = MODELS_CONFIG[name]
        assert info["engine"] == "qwen_asr_hf"
        assert info["model_id"].startswith("Qwen/Qwen3-ASR-") and info["model_id"].endswith("-hf")
        assert not info.get("requires_apple_silicon")  # 这组模型的意义就是别的平台也能跑


def test_old_transformers_makes_the_model_unavailable_with_a_fix(monkeypatch):
    info = MODELS_CONFIG["qwen_asr_small"]
    monkeypatch.setattr(model_catalog, "_has_package", lambda name: True)
    monkeypatch.setattr(model_catalog, "_package_version", lambda name: (4, 57, 6))
    reason = model_catalog.unavailable_reason(info)
    assert "4.57.6" in reason and "5.13" in reason and "setup-env" in reason
    monkeypatch.setattr(model_catalog, "_package_version", lambda name: (5, 13, 0))
    assert model_catalog.unavailable_reason(info) is None
    # 读不出版本:不拦(真不行的话加载时会报出原因)
    monkeypatch.setattr(model_catalog, "_package_version", lambda name: None)
    assert model_catalog.unavailable_reason(info) is None
    monkeypatch.setattr(model_catalog, "_has_package", lambda name: name != "torch")
    assert "torch" in model_catalog.unavailable_reason(info)


def test_package_version_parses_real_versions():
    assert model_catalog._package_version("pytest")[0] >= 7
    assert model_catalog._package_version("definitely-not-installed-xyz") is None
