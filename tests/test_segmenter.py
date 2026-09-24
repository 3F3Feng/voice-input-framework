"""边录边识别(services/segmenter.py 与 /ws/stream 的分段模式)。

假引擎按音频内容「认字」:测试音频是一串 0.8 秒的「字」,每个字是一段常数幅值
(幅值 = 字的编号),字与字之间隔 0.3 秒静音。假引擎把一段音频里出现的幅值按顺序
读出来当作文字。这样段边界切坏了(把一个字劈成两半)就会在结果里重复出现,
丢了音频就会少字,顺序乱了也看得出来。
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

import numpy as np
import pytest

project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from services import segmenter  # noqa: E402

SR = 16000
WORD_S = 0.8
GAP_S = 0.3


def speech(n_words: int, first: int = 1) -> bytes:
    """n 个「字」的音频:第 k 个字是幅值为 (first + k) * 100 的常数段。"""
    parts = []
    for k in range(n_words):
        parts.append(np.full(int(WORD_S * SR), (first + k) * 100, dtype=np.int16))
        parts.append(np.zeros(int(GAP_S * SR), dtype=np.int16))
    return np.concatenate(parts).tobytes()


def words_in(pcm: bytes) -> list[int]:
    """假引擎的「识别」:按顺序读出出现过的幅值(每个连续段算一个字)。"""
    samples = np.frombuffer(pcm, dtype=np.int16)
    prev = np.concatenate(([0], samples[:-1]))
    starts = samples[(samples != prev) & (samples != 0)]
    return [int(v) // 100 for v in starts]


def text_of(pcm: bytes) -> str:
    return " ".join(f"w{w}" for w in words_in(pcm))


@pytest.fixture
def short_segments(monkeypatch):
    """把段长缩到 2–3 秒,测试不用喂几十秒的音频。"""
    monkeypatch.setattr(segmenter, "SEGMENT_MIN_S", 2.0)
    monkeypatch.setattr(segmenter, "SEGMENT_MAX_S", 3.0)
    # find_cut 的默认参数是定义时绑定的,这里一并换掉。
    real = segmenter.find_cut
    monkeypatch.setattr(segmenter, "find_cut", lambda pcm: real(pcm, 2.0, 3.0))


class TestFindCut:
    def test_cut_lands_in_a_pause(self):
        pcm = speech(40)
        cut = segmenter.find_cut(pcm, 2.0, 3.0)
        assert cut % 2 == 0
        assert 2.0 * SR * 2 <= cut <= 3.0 * SR * 2
        # 切点落在静音里:切点两侧的「字」没有被劈开
        samples = np.frombuffer(pcm, dtype=np.int16)
        assert samples[cut // 2] == 0

    def test_short_audio_is_taken_whole(self):
        pcm = speech(1)
        assert segmenter.find_cut(pcm, 2.0, 3.0) == len(pcm)

    def test_all_silence_prefers_the_latest_point(self):
        pcm = np.zeros(4 * SR, dtype=np.int16).tobytes()
        cut = segmenter.find_cut(pcm, 2.0, 3.0)
        assert cut >= int(2.9 * SR) * 2


class TestJoin:
    def test_chinese_is_joined_directly(self):
        assert segmenter.join_segments(["今天开会,", "讨论预算。"]) == "今天开会,讨论预算。"

    def test_english_words_get_a_space(self):
        assert segmenter.join_segments(["hello world.", "Next part"]) == "hello world. Next part"
        assert segmenter.join_segments(["deploy", "rollback"]) == "deploy rollback"

    def test_empty_segments_are_skipped(self):
        assert segmenter.join_segments(["", " 你好 ", "", "世界"]) == "你好世界"
        assert segmenter.join_segments(["", "  "]) == ""


class TestContext:
    def test_hotwords_and_previous_tail(self, monkeypatch):
        monkeypatch.setattr(segmenter, "CONTEXT_TAIL_CHARS", 4)
        assert (
            segmenter.segment_context("石枫、Tauri", "我们今天讨论一下") == "石枫、Tauri\n讨论一下"
        )
        assert segmenter.segment_context(None, "") is None
        assert segmenter.segment_context("石枫", "") == "石枫"

    def test_tail_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(segmenter, "CONTEXT_TAIL_CHARS", 0)
        assert segmenter.segment_context(None, "上一段") is None


class FakeEngine:
    """记下每次调用;可选的延迟模拟模型比实时慢 / 快。"""

    def __init__(self, delay: float = 0.0):
        self.calls: list[tuple[bytes, str | None]] = []
        self.delay = delay

    async def __call__(self, pcm: bytes, context: str | None) -> str:
        self.calls.append((pcm, context))
        await asyncio.sleep(self.delay)
        return text_of(pcm)


def expected(n: int) -> str:
    return " ".join(f"w{k}" for k in range(1, n + 1))


async def feed_realtime(seg, pcm: bytes, frame_s: float = 0.25, speedup: float = 50.0):
    """像录音一样一帧一帧地喂(按 speedup 倍速)。"""
    step = int(frame_s * SR) * 2
    for i in range(0, len(pcm), step):
        seg.feed(pcm[i : i + step])
        await asyncio.sleep(frame_s / speedup)


class TestSegmentedTranscriber:
    @pytest.mark.asyncio
    async def test_segments_while_feeding_and_keeps_every_word_once(self, short_segments):
        engine = FakeEngine()
        seg = segmenter.SegmentedTranscriber(engine)
        await feed_realtime(seg, speech(30))  # 33 秒
        assert len(engine.calls) >= 5  # 录音期间就在转
        before_end = len(engine.calls)
        text = await seg.finish()
        assert text == expected(30)
        assert len(engine.calls) == before_end + 1  # 松手后只转剩下的一段
        # 喂进去的音频一个字节不多不少
        assert sum(len(p) for p, _ in engine.calls) == len(speech(30))

    @pytest.mark.asyncio
    async def test_slow_model_still_keeps_order(self, short_segments):
        engine = FakeEngine(delay=0.2)  # 比「实时」(这里是 50 倍速)慢得多
        seg = segmenter.SegmentedTranscriber(engine)
        await feed_realtime(seg, speech(30))
        assert await seg.finish() == expected(30)

    @pytest.mark.asyncio
    async def test_previous_text_is_passed_as_context(self, short_segments):
        engine = FakeEngine()
        seg = segmenter.SegmentedTranscriber(engine, hotwords="石枫")
        await feed_realtime(seg, speech(12))
        await seg.finish()
        first_ctx = engine.calls[0][1]
        assert first_ctx == "石枫"  # 第一段前面没有文字
        second_ctx = engine.calls[1][1]
        assert second_ctx.startswith("石枫\n") and "w" in second_ctx

    @pytest.mark.asyncio
    async def test_short_recording_is_one_call(self, short_segments):
        engine = FakeEngine()
        seg = segmenter.SegmentedTranscriber(engine)
        seg.feed(speech(2))  # 2.2 秒,不够切一段
        assert await seg.finish() == expected(2)
        assert len(engine.calls) == 1

    @pytest.mark.asyncio
    async def test_cancel_stops_scheduling(self, short_segments):
        engine = FakeEngine(delay=0.05)
        seg = segmenter.SegmentedTranscriber(engine)
        await feed_realtime(seg, speech(10))
        seg.cancel()
        calls = len(engine.calls)
        seg.feed(speech(20))  # 取消之后再来的音频不再转
        await asyncio.sleep(0.2)
        assert len(engine.calls) == calls

    @pytest.mark.asyncio
    async def test_segment_error_surfaces_at_finish(self, short_segments):
        async def boom(pcm, context):
            raise RuntimeError("model gone")

        seg = segmenter.SegmentedTranscriber(boom)
        await feed_realtime(seg, speech(10))
        with pytest.raises(RuntimeError, match="model gone"):
            await seg.finish()


# ── /ws/stream 分段模式 ──


@pytest.fixture
def ws_server(monkeypatch, short_segments):
    """换上假引擎的 STT 服务。返回 (TestClient, 调用记录)。"""
    from fastapi.testclient import TestClient

    import services.stt_server as srv
    from services.stt_server import STTEngine, TranscriptionResult

    engine = STTEngine()
    calls: list[tuple[int, str | None]] = []

    async def fake_transcribe(audio, language="auto", context=None):
        calls.append((len(audio), context))
        await asyncio.sleep(0.01)
        return TranscriptionResult(text=text_of(audio), language="zh")

    monkeypatch.setattr(engine, "transcribe", fake_transcribe)
    monkeypatch.setattr(srv, "engine", engine)
    monkeypatch.setattr(srv, "LLM_ENABLED", False)
    # 服务模块导入时读的是本机的个人词库,测试不能受它影响。
    monkeypatch.setattr(srv, "VOCABULARY", srv.vocabulary.parse([]))
    return TestClient(srv.app), calls


def send_audio(ws, pcm: bytes, frame_s: float = 0.25, pause: float = 0.002):
    step = int(frame_s * SR) * 2
    for i in range(0, len(pcm), step):
        data = base64.b64encode(pcm[i : i + step]).decode()
        ws.send_text(json.dumps({"type": "audio", "data": data}))
        if pause:
            import time

            time.sleep(pause)


def collect_until_final(ws) -> list[dict]:
    msgs = []
    while True:
        msg = json.loads(ws.receive_text())
        msgs.append(msg)
        if msg["type"] in ("result", "error", "done"):
            return msgs


class TestWebSocketIncremental:
    def test_ready_advertises_incremental(self, ws_server):
        client, _ = ws_server
        with client.websocket_connect("/ws/stream") as ws:
            ready = json.loads(ws.receive_text())
            assert ready["incremental"] is True
            ws.send_text(json.dumps({"type": "config", "language": "zh", "incremental": True}))
            ack = json.loads(ws.receive_text())
            assert ack == {"type": "config_ack", "language": "zh", "incremental": True}
            ws.send_text(json.dumps({"type": "end"}))

    def test_incremental_session_returns_whole_text(self, ws_server):
        client, calls = ws_server
        pcm = speech(20)
        with client.websocket_connect("/ws/stream") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "config", "language": "zh", "incremental": True}))
            json.loads(ws.receive_text())
            send_audio(ws, pcm)
            ws.send_text(json.dumps({"type": "end"}))
            msgs = collect_until_final(ws)
        final = msgs[-1]
        assert final["type"] == "result"
        assert final["text"] == expected(20)
        stt = next(m for m in msgs if m["type"] == "stt_result")
        assert stt["text"] == expected(20)
        assert len(calls) > 1  # 真的分段了
        assert sum(n for n, _ in calls) == len(pcm)
        assert any(m["type"] == "segment" for m in msgs)

    def test_vocabulary_rules_apply_across_the_joined_text(self, ws_server, monkeypatch):
        import services.stt_server as srv
        from services import vocabulary

        monkeypatch.setattr(srv, "VOCABULARY", vocabulary.parse(["w1 w2 => 开头", "石枫"]))
        client, calls = ws_server
        with client.websocket_connect("/ws/stream") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "config", "language": "zh", "incremental": True}))
            json.loads(ws.receive_text())
            send_audio(ws, speech(12))
            ws.send_text(json.dumps({"type": "end"}))
            final = collect_until_final(ws)[-1]
        assert final["text"].startswith("开头 w3")
        # 热词照样作为上下文交给每一段
        assert all(ctx and ctx.startswith("石枫、开头") for _, ctx in calls)

    def test_silent_incremental_recording_is_no_speech(self, ws_server):
        client, _ = ws_server
        with client.websocket_connect("/ws/stream") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "config", "language": "zh", "incremental": True}))
            json.loads(ws.receive_text())
            send_audio(ws, np.zeros(8 * SR, dtype=np.int16).tobytes())
            ws.send_text(json.dumps({"type": "end"}))
            msgs = collect_until_final(ws)
        # 和整段转写一样:空文本的 result,客户端据此显示「没有录到声音」
        assert msgs[-1]["type"] == "result" and msgs[-1]["text"] == ""

    def test_cancel_discards_everything(self, ws_server):
        client, calls = ws_server
        with client.websocket_connect("/ws/stream") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "config", "language": "zh", "incremental": True}))
            json.loads(ws.receive_text())
            send_audio(ws, speech(10))
            ws.send_text(json.dumps({"type": "cancel"}))
            msgs = []
            try:
                while True:
                    msgs.append(json.loads(ws.receive_text()))
            except Exception:
                pass  # 服务端直接关连接
        assert not any(m["type"] in ("result", "stt_result", "done") for m in msgs)

    def test_old_client_is_unchanged(self, ws_server):
        """老客户端:config 不带 incremental、松手后一次性上传 —— 照旧整段转一次。"""
        client, calls = ws_server
        pcm = speech(20)
        with client.websocket_connect("/ws/stream") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "config", "language": "zh"}))
            ack = json.loads(ws.receive_text())
            assert ack["incremental"] is False
            send_audio(ws, pcm, pause=0)
            ws.send_text(json.dumps({"type": "end"}))
            final = collect_until_final(ws)[-1]
        assert final["text"] == expected(20)
        assert calls == [(len(pcm), None)]

    def test_disconnect_without_end_transcribes_nothing(self, ws_server):
        client, calls = ws_server
        with client.websocket_connect("/ws/stream") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "config", "language": "zh"}))
            json.loads(ws.receive_text())
            send_audio(ws, speech(3), pause=0)
        # 连接关了之后给服务端一点时间处理断开
        import time

        time.sleep(0.2)
        assert calls == []
