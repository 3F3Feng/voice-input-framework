"""Qwen3-ASR 量化版(services/qwen_asr_gguf.py):Windows / Linux 上的中文识别。

真模型在本机(M3 Max,Metal 和纯 CPU)实测过,CI 在 Linux / Windows 上也真跑一次;
这里用假的 llama.cpp 钉住接线:输出怎么拆、语言怎么指定、热词怎么传、长音频怎么切。
"""

import io
import wave

import numpy as np
import pytest

from services import qwen_asr_gguf as q
from services.stt_engine import _infer_sync, resolve_language


def test_output_is_split_into_language_and_text():
    assert q.parse_output("language Chinese<asr_text>今天下午三点开会。") == (
        "今天下午三点开会。",
        "Chinese",
    )
    assert q.parse_output("language English<asr_text> Hello there. <|im_end|>") == (
        "Hello there.",
        "English",
    )
    # 指定了语言时前半截是我们填的,输出里只有正文
    assert q.parse_output("今天下午三点开会。") == ("今天下午三点开会。", None)
    # 静音:模型说不出语言
    assert q.parse_output("language None<asr_text>") == ("", None)
    assert q.parse_output("") == ("", None)
    # 正文里可以有换行
    assert q.parse_output("language Chinese<asr_text>第一行\n第二行")[0] == "第一行\n第二行"


def test_audio_becomes_a_16_bit_mono_wav():
    audio = np.array([0.0, 0.5, -0.5, 2.0, -2.0], dtype=np.float32)  # 超出范围的要削掉,不能绕回去
    with wave.open(io.BytesIO(q.to_wav_bytes(audio, 16000))) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (
            1,
            2,
            16000,
            5,
        )
        samples = np.frombuffer(w.readframes(5), dtype="<i2")
    assert list(samples) == [0, 16383, -16383, 32767, -32767]


def test_token_budget_grows_with_audio_and_is_capped():
    assert q.token_budget(0) == q.MIN_NEW_TOKENS
    assert q.token_budget(28) == q.MIN_NEW_TOKENS + 28 * q.TOKENS_PER_SECOND
    assert q.token_budget(3600) == q.MAX_NEW_TOKENS
    # 一段(上限 MAX_CHUNK_SECONDS)的音频 + 输出装得进上下文窗口(音频约每秒 13 个 token)
    assert q.MAX_CHUNK_SECONDS * 13 + q.token_budget(q.MAX_CHUNK_SECONDS) < q.N_CTX


def test_long_audio_is_cut_at_the_quietest_spot():
    sr = 1000
    audio = np.ones(25 * sr, dtype=np.float32)
    audio[8_300:8_400] = 0.0  # 一小段安静,在第一段上限(10 秒)之前
    pieces = q.split_long(audio, sr, max_seconds=10)
    assert sum(len(p) for p in pieces) == len(audio)  # 一个采样都不丢
    assert all(len(p) <= 10 * sr for p in pieces)
    assert 8_300 <= len(pieces[0]) <= 8_400  # 切在安静的地方,而不是硬切在 10 秒
    # 短的原样返回
    short = np.zeros(5 * sr, dtype=np.float32)
    assert q.split_long(short, sr, max_seconds=10) == [short]


def test_pieces_are_joined_without_gluing_english_words():
    assert q._join(["今天开会。", "明天放假。"]) == "今天开会。明天放假。"
    assert q._join(["deploy to", "staging"]) == "deploy to staging"
    assert q._join(["版本 2", "7 发布"]) == "版本 2 7 发布"
    assert q._join(["hello.", "World"]) == "hello.World"


class FakeHandler:
    pending: dict = {}
    prefill = ""


class FakeLlama:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.closed = False

    def create_chat_completion(self, **kwargs):
        # 调用的那一刻音频必须已经放好
        self.calls.append(
            {**kwargs, "pending": dict(self.handler.pending), "prefill": self.handler.prefill}
        )
        return {"choices": [{"message": {"content": self.replies.pop(0)}}]}

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_fd_redirect(monkeypatch):
    """真的 `_quiet()` 会重定向进程的 stdout / stderr(装了 llama.cpp 时);测试里不需要。"""
    import contextlib

    monkeypatch.setattr(q, "_quiet", contextlib.nullcontext)


def make_engine(*replies):
    engine = object.__new__(q.QwenASRGguf)
    engine._handler = FakeHandler()
    engine._llm = FakeLlama(replies)
    engine._llm.handler = engine._handler
    engine.accelerator, engine.accelerator_note = "gpu", None
    return engine


def test_auto_detect_sends_only_the_audio_and_reports_the_language():
    engine = make_engine("language Chinese<asr_text>今天下午三点开会。")
    audio = np.zeros(16000 * 4, dtype=np.float32)
    assert engine.transcribe(audio, 16000, None) == ("今天下午三点开会。", "Chinese")
    (call,) = engine._llm.calls
    assert [m["role"] for m in call["messages"]] == ["user"]  # 没有热词就没有系统提示
    assert call["prefill"] == ""
    (wav,) = call["pending"].values()
    assert wav[:4] == b"RIFF"
    assert call["max_tokens"] == q.token_budget(4)
    # 听写要确定的结果;重复惩罚会把真说了两遍的话改掉
    assert call["temperature"] == 0.0 and call["repeat_penalty"] == 1.0
    # 用完就清掉,不让一段音频一直留在内存里
    assert engine._handler.pending == {}


def test_forced_language_and_hotwords_are_forwarded():
    engine = make_engine("石枫在用 Tauri")
    text, spoken = engine.transcribe(
        np.zeros(8000, dtype=np.float32), 16000, "Chinese", context="石枫 Tauri"
    )
    assert (text, spoken) == ("石枫在用 Tauri", None)
    (call,) = engine._llm.calls
    assert call["prefill"] == "language Chinese<asr_text>"
    assert call["messages"][0] == {"role": "system", "content": "石枫 Tauri"}


def test_long_audio_is_transcribed_piece_by_piece(monkeypatch):
    monkeypatch.setattr(q, "MAX_CHUNK_SECONDS", 1)
    audio = np.ones(int(2.5 * 16000), dtype=np.float32)
    pieces = q.split_long(audio, 16000)
    assert len(pieces) >= 3 and sum(len(p) for p in pieces) == len(audio)
    # 最后一段是静音(模型什么都没认出来):不往结果里加东西
    replies = [f"language Chinese<asr_text>第{i}段。" for i in range(len(pieces) - 1)]
    engine = make_engine(*replies, "language None<asr_text>")
    text, spoken = engine.transcribe(audio, 16000, None)
    assert text == "".join(f"第{i}段。" for i in range(len(pieces) - 1))
    assert spoken == "Chinese"
    assert len(engine._llm.calls) == len(pieces)


def test_a_closed_engine_refuses_instead_of_crashing():
    engine = make_engine()
    llm = engine._llm
    engine.close()
    engine.close()  # 重复调没有副作用
    assert llm.closed
    with pytest.raises(RuntimeError):
        engine.transcribe(np.zeros(1600, dtype=np.float32), 16000, None)


@pytest.mark.parametrize(
    ("requested", "reply", "expected"),
    [
        (None, "language Chinese<asr_text>ok", "zh"),  # 自动检测:语言名换回代码
        (None, "language Cantonese<asr_text>ok", "yue"),
        ("Chinese", "ok", "zh"),  # 指定了语言:模型不报,用请求的那个
        (None, "language Swahili<asr_text>ok", "Swahili"),  # 表里没有的语言名原样带回
        (None, "ok", None),
    ],
)
def test_infer_maps_language_names_back_to_codes(requested, reply, expected):
    engine = make_engine(reply)
    text, lang = _infer_sync(
        engine, "qwen_asr_gguf", np.zeros(1600, dtype=np.float32), 16000, requested
    )
    assert (text, lang) == ("ok", expected)


def test_client_language_codes_become_qwen_names_for_this_engine():
    assert resolve_language("zh", "qwen_asr_gguf") == "Chinese"
    assert resolve_language("yue", "qwen_asr_gguf") == "Cantonese"
    assert resolve_language("auto", "qwen_asr_gguf") is None


def test_missing_llama_cpp_makes_the_model_unavailable_with_a_fix(monkeypatch):
    from services import model_catalog
    from shared.model_registry import MODELS_CONFIG

    info = MODELS_CONFIG["qwen_asr_small"]
    monkeypatch.setattr(model_catalog, "_has_package", lambda name: name != "llama_cpp")
    reason = model_catalog.unavailable_reason(info)
    assert "llama.cpp" in reason and "setup-env" in reason
    monkeypatch.setattr(model_catalog, "_has_package", lambda name: True)
    assert model_catalog.unavailable_reason(info) is None
