"""边录边识别:录音期间把已经收到的音频按段转写,松手时只剩最后一小段。

以前客户端松手才建 WS、一次性上传,服务端收齐之后整段转写:说了 3 分钟,松手
还要干等整段 3 分钟音频的推理(外加 LLM)。现在客户端按下就开始传(见
gui/src-tauri/src/stt.rs 的 `LiveSession`),服务端每攒够一段就在后台先转掉,
松手时只剩最后不到一段。

- **切在哪儿**:和 mlx-audio 的 `split_audio_into_chunks` 同一个办法 —— 在目标
  切点附近找 100 ms 滑动窗口能量最低的地方,尽量切在停顿上,不把一个字劈成两半。
  区别是这里只能往回找(后面的音频还没录到),所以等攒到窗口的右端才切。
- **顺序**:同一时刻最多一段在转写(模型本来就只有一个线程,见
  `STTEngine._model_thread`),转完一段才排下一段,结果天然按顺序。
- **上下文**:上一段的末尾一小截和热词一起作为识别上下文(Qwen3-ASR 的
  system prompt / whisper 的 initial_prompt),段首的同音字、专名跟得上前文。
- **取消**:客户端放弃或断开时不再排新的段;正在跑的那一段(最多几秒)跑完即丢。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import numpy as np

logger = logging.getLogger("stt-server")

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2

#: 攒够这么多秒还没转的音频才切一段;切点在 [SEGMENT_MIN_S, SEGMENT_MAX_S] 里找。
#: 段太短,每段都要付一次固定开销,段首段尾也更容易认错;段太长,松手时剩下的
#: 尾巴就长,省下的时间少。28 秒也正好落在 Whisper 的 30 秒窗口以内。
SEGMENT_MIN_S = 18.0
SEGMENT_MAX_S = 28.0
#: 找切点用的能量窗口。
CUT_WINDOW_MS = 100
#: 上一段末尾取多少个字作为下一段的识别上下文;0 表示不传。
CONTEXT_TAIL_CHARS = 60

TranscribeFn = Callable[[bytes, str | None], Awaitable[str]]


def find_cut(pcm: bytes, min_s: float = SEGMENT_MIN_S, max_s: float = SEGMENT_MAX_S) -> int:
    """在 `pcm`(16 kHz i16 单声道)的 [min_s, max_s] 里找能量最低的位置,返回字节偏移。

    和 mlx-audio 的 `split_audio_into_chunks` 同一个办法。试过「切在最长的停顿正中」
    (想让段尾落在句号上):3 分钟样本上和整段转写相比标点差异反而从 9 处涨到 20 处,
    字一个不差 —— 所以还是用这个最简单的。

    返回值总是偶数(不会把一个采样劈开)。音频不够 max_s 时按实际长度找。
    """
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16)
    lo = int(min_s * SAMPLE_RATE)
    hi = min(int(max_s * SAMPLE_RATE), len(samples))
    win = int(CUT_WINDOW_MS * SAMPLE_RATE / 1000)
    if hi - lo <= win:
        return hi * BYTES_PER_SAMPLE
    region = samples[lo:hi].astype(np.float64) / 32768.0
    energy = np.convolve(region**2, np.ones(win) / win, mode="valid")
    # 能量一样低(比如一整段静音)时取最靠后的:这一段尽量长,剩下的尾巴尽量短。
    idx = len(energy) - 1 - int(np.argmin(energy[::-1]))
    return (lo + idx + win // 2) * BYTES_PER_SAMPLE


def _is_ascii_word_char(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def join_segments(texts: list[str]) -> str:
    """把各段文字接起来。

    中文段之间直接相连;两边都是英文单词(或者前一段以英文标点结尾、后一段以
    英文开头)时补一个空格,不然 "hello" + "world" 会粘成 "helloworld"。
    """
    out = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if out and _is_ascii_word_char(text[0]):
            last = out[-1]
            if _is_ascii_word_char(last) or last in ".,!?;:":
                out += " "
        out += text
    return out


def segment_context(hotwords: str | None, previous_text: str) -> str | None:
    """下一段的识别上下文:热词在前,上一段的末尾一小截在后。"""
    tail = previous_text.strip()[-CONTEXT_TAIL_CHARS:] if CONTEXT_TAIL_CHARS > 0 else ""
    parts = [p for p in (hotwords, tail) if p]
    return "\n".join(parts) or None


class SegmentedTranscriber:
    """录音期间分段转写。`feed` 喂音频,`finish` 等所有段转完并接成全文,`cancel` 放弃。

    `transcribe(pcm, context)` 返回这一段的文字(静音 / 幻觉由引擎自己挡成空串)。
    `on_segment(index, text, audio_s)` 每转完一段调用一次(发给客户端当进度用),可省。
    """

    def __init__(
        self,
        transcribe: TranscribeFn,
        hotwords: str | None = None,
        on_segment: Callable[[int, str, float], Awaitable[None]] | None = None,
    ):
        self._transcribe = transcribe
        self._hotwords = hotwords
        self._on_segment = on_segment
        #: 还没切出去的音频(已经切出去的那部分不再留着)。
        self._pending = bytearray()
        self._texts: list[str] = []
        self._task: asyncio.Task | None = None
        self._error: BaseException | None = None
        self._finishing = False
        self.cancelled = False
        #: 已经切出去交给模型的秒数(日志用)。
        self.segmented_s = 0.0

    @property
    def pending_s(self) -> float:
        return len(self._pending) / (SAMPLE_RATE * BYTES_PER_SAMPLE)

    def feed(self, chunk: bytes) -> None:
        if self.cancelled or self._finishing:
            return
        self._pending.extend(chunk)
        self._maybe_schedule()

    def _maybe_schedule(self) -> None:
        if self.cancelled or self._finishing or self._error is not None:
            return
        if self._task is not None and not self._task.done():
            return
        if self.pending_s < SEGMENT_MAX_S:
            return
        cut = find_cut(bytes(self._pending))
        segment = bytes(self._pending[:cut])
        del self._pending[:cut]
        self._task = asyncio.ensure_future(self._run(segment))

    def _context(self) -> str | None:
        previous = next((t for t in reversed(self._texts) if t.strip()), "")
        return segment_context(self._hotwords, previous)

    async def _run(self, segment: bytes) -> None:
        audio_s = len(segment) / (SAMPLE_RATE * BYTES_PER_SAMPLE)
        try:
            text = await self._transcribe(segment, self._context())
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 留到 finish 时再抛,客户端照常收到 error
            logger.error(f"Segment transcription failed: {e}")
            self._error = e
            return
        if self.cancelled:
            return
        self._texts.append(text)
        self.segmented_s += audio_s
        logger.info(
            f"Segment {len(self._texts)} done: {audio_s:.1f}s audio, "
            f"{self.pending_s:.1f}s still pending"
        )
        if self._on_segment is not None:
            try:
                await self._on_segment(len(self._texts) - 1, text, audio_s)
            except Exception:  # noqa: BLE001 - 进度发不出去不影响转写
                pass
        # 录音期间模型比说话慢时,积压的音频接着切下一段。先把自己摘掉,
        # 否则 `_maybe_schedule` 看见「还有一段没跑完」(就是本段)不肯排。
        self._task = None
        self._maybe_schedule()

    async def finish(self) -> str:
        """录音结束:等正在转的那段,再把剩下的一次转完,返回接好的全文。"""
        self._finishing = True
        task = self._task
        if task is not None:
            await task
        if self._error is not None:
            raise self._error
        if self._pending:
            rest = bytes(self._pending)
            self._pending.clear()
            text = await self._transcribe(rest, self._context())
            self._texts.append(text)
        return join_segments(self._texts)

    def cancel(self) -> None:
        """客户端放弃 / 断开:不再排新段,正在跑的那段结果丢掉。"""
        self.cancelled = True
        self._pending.clear()
        if self._task is not None and not self._task.done():
            # 模型线程上的那次推理停不下来(最多一段的时间),但结果不会再被用到。
            self._task.cancel()
