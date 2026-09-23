"""
识别语言的换算(services/stt_engine.resolve_language)。

客户端一律发 "zh" / "en" / "yue" / "ja" / "ko" / "auto" 这样的代码,各引擎要的写法
却不一样:Whisper 要代码,Qwen3-ASR 要英文名("Chinese")。以前代码原样传给 Qwen,
提示词变成 `language zh<asr_text>`,指定语言等于白指定。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from services.stt_engine import (  # noqa: E402
    QWEN_LANGUAGE_NAMES,
    STTEngine,
    _whisper_supports_yue,
    resolve_language,
)

# 设置界面「识别语言」下拉框里的全部选项(App.vue 的 LANGUAGE_OPTIONS)。
UI_CODES = ["zh", "en", "yue", "ja", "ko"]


class TestResolveLanguage:
    @pytest.mark.parametrize("value", ["auto", "AUTO", "", None, "  "])
    def test_auto_means_detect(self, value):
        for model_type in ("qwen_asr_mlx_native", "whisper_mlx", "whisper_turbo", None):
            assert resolve_language(value, model_type) is None

    def test_qwen_gets_language_names(self):
        expected = {
            "zh": "Chinese",
            "en": "English",
            "yue": "Cantonese",
            "ja": "Japanese",
            "ko": "Korean",
        }
        for code, name in expected.items():
            assert resolve_language(code, "qwen_asr_mlx_native") == name

    def test_every_ui_option_maps_for_qwen(self):
        # 界面上能选的每一项,Qwen 都得有对应的名字,不能漏成原样的代码
        for code in UI_CODES:
            assert code in QWEN_LANGUAGE_NAMES

    def test_whisper_gets_codes(self):
        for code in UI_CODES:
            for model_type in ("whisper_mlx", "whisper_turbo", "whisper_cpp"):
                assert resolve_language(code, model_type) == code

    def test_names_and_regions_are_normalized(self):
        # 旧配置 / 直接调 API 的人可能写英文名或带地区
        assert resolve_language("Chinese", "whisper_mlx") == "zh"
        assert resolve_language("chinese", "qwen_asr_mlx_native") == "Chinese"
        assert resolve_language("zh-CN", "whisper_turbo") == "zh"
        assert resolve_language("zh_TW", "qwen_asr_mlx_native") == "Chinese"

    def test_unknown_language_passes_through_for_qwen(self):
        # mlx-audio 自己还会按名字再匹配一次,认不出的别在这里吞掉
        assert resolve_language("Swedish", "qwen_asr_mlx_native") == "Swedish"

    def test_cantonese_falls_back_on_small_whisper(self):
        # 只有 large-v3 一代有 yue token;小模型拿到 "yue" 会直接抛错
        assert resolve_language("yue", "whisper_turbo", supports_yue=False) == "zh"
        assert resolve_language("yue", "whisper_turbo", supports_yue=True) == "yue"
        # Qwen 不受这个开关影响
        assert resolve_language("yue", "qwen_asr_mlx_native", supports_yue=False) == "Cantonese"

    def test_whisper_yue_support_detection(self):
        from shared.model_registry import MODELS_CONFIG

        assert _whisper_supports_yue(MODELS_CONFIG["whisper_mlx"])
        assert _whisper_supports_yue(MODELS_CONFIG["whisper_mlx_turbo"])
        assert _whisper_supports_yue(MODELS_CONFIG["whisper_turbo"])
        assert _whisper_supports_yue(MODELS_CONFIG["whisper_cpp_large"])
        assert not _whisper_supports_yue(MODELS_CONFIG["whisper_small"])
        assert not _whisper_supports_yue(MODELS_CONFIG["whisper_mlx_medium"])
        assert not _whisper_supports_yue(None)


class _FakeQwen:
    """记下 transcribe 收到的 language,模拟 Qwen3ASRMLXNativeEngine 的接口。"""

    def __init__(self):
        self.seen = []

    def transcribe_sync(self, audio_array, language, context=None):
        # STTEngine 在模型线程上调同步接口(R9)
        self.seen.append(language)
        return "ok", ""

    async def transcribe(self, audio, language, sample_rate):
        self.seen.append(language)

        class R:
            text = "你好"

        R.language = "Chinese"
        return R()


class _FakeWhisperPipeline:
    def __init__(self):
        self.seen = []

    def __call__(self, audio, generate_kwargs, return_timestamps=False):
        self.seen.append(generate_kwargs["language"])
        return {"text": "hi"}


def _loaded_engine(model_name, model, model_type):
    engine = STTEngine(default_model=model_name)
    engine._model = model
    engine._model_type = model_type
    engine._is_loaded = True
    return engine


# 不能用全零:静音会被闸门挡下、根本不跑模型(R25)
AUDIO = (3000 * np.sin(2 * np.pi * 220 * np.arange(1600) / 16000)).astype(np.int16).tobytes()


class TestTranscribePassesResolvedLanguage:
    @pytest.mark.asyncio
    async def test_qwen_receives_name(self):
        fake = _FakeQwen()
        engine = _loaded_engine("qwen_asr_mlx_native_small", fake, "qwen_asr_mlx_native")
        await engine.transcribe(AUDIO, language="zh")
        await engine.transcribe(AUDIO, language="auto")
        assert fake.seen == ["Chinese", "auto"]

    @pytest.mark.asyncio
    async def test_small_whisper_cantonese_becomes_zh(self):
        fake = _FakeWhisperPipeline()
        engine = _loaded_engine("whisper_small", fake, "whisper_turbo")
        await engine.transcribe(AUDIO, language="yue")
        await engine.transcribe(AUDIO, language="en")
        await engine.transcribe(AUDIO, language="auto")
        assert fake.seen == ["zh", "en", None]
