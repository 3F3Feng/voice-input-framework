"""Qwen3-ASR 的量化版(GGUF,8 位),跑在 llama.cpp 上。Windows / Linux 上的中文识别。

非 Apple 平台以前只有 Whisper,中文明显不如 Qwen3-ASR;Qwen3-ASR 只有 MLX 版
(`qwen_asr_mlx_native*`),只能在 Apple Silicon 上跑。2.7.0 先用 transformers 接了半精度的
原版权重,能用,但 0.6B 的模型要占和 Whisper Large 差不多的显存——听写这个场景用不着
完整权重。这里换成 llama.cpp 官方转的 8 位量化版(`ggml-org/Qwen3-ASR-*-GGUF`):

- 权重小三分之一(0.6B:1.57 GB → 1.02 GB;1.7B:4.07 GB → 2.52 GB),也不再需要 PyTorch 的
  CUDA 运行环境常驻显存;
- 本机(M3 Max)对比:同一批录音识别结果一样,速度快一倍左右;
- 跑它的就是给 LLM 后处理装的那个 llama.cpp,所以 NVIDIA 走 CUDA、AMD / Intel 走 Vulkan、
  没有显卡走 CPU,都是同一段代码(装哪一种见 scripts/setup-env.*)。

模型由两个文件组成:语言模型本体,和把音频变成它能读的向量的「投影器」(mmproj)。
音频经 llama.cpp 的多模态库(mtmd)送进去;llama-cpp-python 现成的 `MTMDChatHandler`
是给图像模型写的,这里借它的流程,改掉三处只适用于图像的地方(见 `_make_handler`)。

模型相关的东西都收在这个类里;`services/stt_engine.py` 只管在模型线程上调它。
"""

from __future__ import annotations

import io
import logging
import re
import wave
from typing import Any

from shared.i18n import bi

logger = logging.getLogger("stt-server")

#: 上下文窗口(token)。音频大约每秒 13 个 token,再加上输出;8K 能装下四分钟的一段。
N_CTX = 8192

#: 一次送进模型的音频上限(秒)。更长的(转写文件时才会遇到)切成几段依次识别。
#: 边录边传时服务端本来就按 18–28 秒分段(services/segmenter.py),到不了这里。
MAX_CHUNK_SECONDS = 180

#: 生成长度上限:每秒音频给 15 个 token(中文连续说话实测每秒 4–6 个 token,
#: 英文快语速 8 个左右),再加一点起步量。给少了长句会被截断,给多了没有代价
#: ——模型说完会自己停。
TOKENS_PER_SECOND = 15
MIN_NEW_TOKENS = 128
MAX_NEW_TOKENS = 3000

#: 模型原始输出的样子:`language Chinese<asr_text>今天下午三点开会。`
#: 指定了语言时前半截是我们自己填进去的,输出里只有正文。
_OUTPUT = re.compile(r"^\s*language\s+(?P<lang>[^<]*?)\s*<asr_text>(?P<text>.*)$", re.DOTALL)

#: 送给聊天处理器的「音频地址」。真正的字节放在处理器的 `pending` 里,不走 base64。
_AUDIO_URL = "vif-audio://clip"


def token_budget(seconds: float) -> int:
    return int(min(MAX_NEW_TOKENS, MIN_NEW_TOKENS + max(0.0, seconds) * TOKENS_PER_SECOND))


def parse_output(raw: str) -> tuple[str, str | None]:
    """模型原始输出 → (正文, 语言名)。没有 `language …<asr_text>` 前缀时语言为 None。"""
    raw = raw.replace("<|im_end|>", "").replace("<|endoftext|>", "")
    m = _OUTPUT.match(raw)
    if not m:
        return raw.strip(), None
    lang = m.group("lang").strip()
    return m.group("text").strip(), (lang if lang and lang.lower() != "none" else None)


def to_wav_bytes(audio, sample_rate: int) -> bytes:
    """float32(-1..1)的单声道音频 → 内存里的 16 位 WAV。mtmd 认的是音频文件,不是裸采样。"""
    import numpy as np

    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def split_long(audio, sample_rate: int, max_seconds: float | None = None) -> list[Any]:
    """太长的音频切成几段。每段尽量切在最安静的地方(最后 10 秒里能量最低的 100 毫秒),

    免得把一个字劈成两半。短于上限的原样返回。
    """
    import numpy as np

    limit = int((MAX_CHUNK_SECONDS if max_seconds is None else max_seconds) * sample_rate)
    if len(audio) <= limit:
        return [audio]
    window = max(1, sample_rate // 10)
    search = min(limit // 2, 10 * sample_rate)
    pieces = []
    start = 0
    while len(audio) - start > limit:
        lo = start + limit - search
        tail = np.asarray(audio[lo : start + limit], dtype=np.float32)
        frames = len(tail) // window
        if frames:
            energy = np.square(tail[: frames * window]).reshape(frames, window).sum(axis=1)
            cut = lo + int(np.argmin(energy)) * window + window // 2
        else:
            cut = start + limit
        pieces.append(audio[start:cut])
        start = cut
    pieces.append(audio[start:])
    return pieces


class _AudioProbe:
    """把「这个模型支持视觉吗」换成「支持音频吗」的 mtmd 模块代理。

    `MTMDChatHandler` 初始化时要求投影器支持图像,不支持就报错;Qwen3-ASR 的投影器
    只支持音频。别的函数原样转给真的模块。
    """

    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        return getattr(self._module, name)

    def mtmd_support_vision(self, ctx) -> bool:
        return bool(self._module.mtmd_support_audio(ctx))


def _make_handler(mmproj_path: str, use_gpu: bool):
    """造一个能收音频的聊天处理器。类定义放在函数里,是因为要 import llama_cpp 才有基类。"""
    import llama_cpp.mtmd_cpp as mtmd_cpp
    from llama_cpp.llama_chat_format import MTMDChatHandler

    class AudioChatHandler(MTMDChatHandler):
        #: 这一次要识别的音频:地址 → WAV 字节。
        pending: dict[str, bytes] = {}
        #: 接在「轮到助手说话」后面的开头。指定语言时填 `language Chinese<asr_text>`,
        #: 模型就只往下写正文(和官方 transformers 实现的做法一样)。
        prefill: str = ""

        # 1. 这个模型的对话模板把消息内容当字符串拼(`role + '\n' + content`),
        #    基类给的是「片段列表」;把片段拼成字符串,音频那一片换成占位符。
        @classmethod
        def _convert_message_for_template(cls, message, media_marker):
            converted = dict(message)
            content = converted.get("content")
            if isinstance(content, list):
                converted["content"] = "".join(
                    media_marker if part.get("type") == "image_url" else part.get("text", "")
                    for part in content
                )
            return converted

        # 2. 音频字节直接从内存里拿,不经 data: URL 绕一圈 base64。
        def load_image(self, image_url: str) -> bytes:
            return self.pending[image_url]

        # 3. 指定语言:在模板渲染完的文本后面接上开头。
        def _postprocess_template_text(self, text, image_urls, media_marker):
            return super()._postprocess_template_text(text, image_urls, media_marker) + self.prefill

    handler = AudioChatHandler(clip_model_path=mmproj_path, verbose=False, use_gpu=use_gpu)
    handler._mtmd_cpp = _AudioProbe(mtmd_cpp)
    return handler


class QwenASRGguf:
    """加载好的 Qwen3-ASR(GGUF)。加载和推理都必须在同一个线程上调。"""

    def __init__(self, repo: str, model_file: str, mmproj_file: str):
        from shared import llama_runtime, llm_backend

        # CUDA 版的 llama.cpp 要的 CUDA 运行库在 PyTorch 的目录里,得先指给它。
        llama_runtime.prepare()
        import llama_cpp
        from huggingface_hub import hf_hub_download

        model_path = hf_hub_download(repo_id=repo, filename=model_file)
        mmproj_path = hf_hub_download(repo_id=repo, filename=mmproj_file)

        #: 模型跑在哪(`gpu` / `cpu`)和为什么,/health 的 hardware 里会报出来。
        self.accelerator = "cpu"
        self.accelerator_note: str | None = None
        self._llm = None
        self._handler = None

        def open_model(gpu: bool) -> None:
            handler = _make_handler(mmproj_path, use_gpu=gpu)
            self._llm = llama_cpp.Llama(
                model_path=model_path,
                chat_handler=handler,
                n_ctx=N_CTX,
                n_gpu_layers=-1 if gpu else 0,
                verbose=False,
            )
            self._handler = handler

        # llama.cpp 认不认得出显卡:装的是 CPU 版、没有驱动、没有 Vulkan 运行库时都是 False。
        if bool(llama_cpp.llama_supports_gpu_offload()):
            try:
                open_model(gpu=True)
                self.accelerator = "gpu"
            except Exception as e:  # noqa: BLE001 - 显存不够、驱动出错:退回 CPU 总比没有强
                logger.warning(f"Loading Qwen3-ASR on the GPU failed ({e}); falling back to CPU")
                self.close()
                open_model(gpu=False)
                self.accelerator_note = bi(
                    f"在显卡上加载失败({e}),已退回 CPU;多半是显存不够",
                    f"loading on the GPU failed ({e}), so it fell back to the CPU; "
                    f"most likely not enough VRAM",
                )
        else:
            open_model(gpu=False)
            hint = llm_backend.setup_hint()
            self.accelerator_note = bi(
                f"llama.cpp 没有认出可用的显卡(装的是 CPU 版,或者显卡驱动不可用);"
                f"有独立显卡的话重跑 {hint} 换成显卡版",
                f"llama.cpp found no usable GPU (the CPU build is installed, or the GPU driver "
                f"isn't available); if you have a discrete GPU, rerun {hint} to get the GPU build",
            )
        logger.info(f"Qwen3-ASR (GGUF) on {self.accelerator.upper()}")

    def close(self) -> None:
        """释放模型(显存)。换模型、服务退出时调;重复调没有副作用。"""
        llm, self._llm, self._handler = self._llm, None, None
        if llm is not None:
            try:
                llm.close()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"closing Qwen3-ASR (GGUF) failed: {e}")

    def transcribe(
        self,
        audio,
        sample_rate: int,
        language: str | None,
        context: str | None = None,
    ) -> tuple[str, str | None]:
        """转写一段 16 kHz 的 float32 音频,返回 (文本, 模型报的语言名)。

        `language` 是 Qwen 的语言名("Chinese"),None = 自动检测;指定了语言时模型不再报
        语言,第二项为 None。`context` 是个人词库的热词,当系统提示传进去。
        """
        texts: list[str] = []
        spoken: str | None = None
        for piece in split_long(audio, sample_rate):
            text, lang = self._transcribe_piece(piece, sample_rate, language, context)
            if text:
                texts.append(text)
            spoken = spoken or lang
        return "".join(texts) if len(texts) < 2 else _join(texts), spoken

    def _transcribe_piece(self, audio, sample_rate, language, context) -> tuple[str, str | None]:
        if self._llm is None or self._handler is None:
            raise RuntimeError("Qwen3-ASR (GGUF) model is closed")
        messages: list[dict[str, Any]] = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append(
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": _AUDIO_URL}}],
            }
        )
        self._handler.pending = {_AUDIO_URL: to_wav_bytes(audio, sample_rate)}
        self._handler.prefill = f"language {language}<asr_text>" if language else ""
        try:
            # llama.cpp 的多模态库每处理一段音频都往 stderr 打十来行调试信息(分词、编码
            # 耗时……),不受 verbose=False 管。每说一句话日志里多十几行,真正的报错反而
            # 被淹没,所以这一小段时间把它的输出关掉。
            with _quiet():
                out = self._llm.create_chat_completion(
                    messages=messages,
                    max_tokens=token_budget(len(audio) / max(1, sample_rate)),
                    # 听写要的是确定的结果;重复惩罚会把真说了两遍的话改掉,关掉。
                    temperature=0.0,
                    repeat_penalty=1.0,
                )
        finally:
            self._handler.pending = {}
        raw = out["choices"][0]["message"].get("content") or ""
        return parse_output(raw)


def _quiet():
    """llama.cpp 自带的「把 C 库的输出关掉」的上下文;拿不到(版本变了)就什么都不做。"""
    try:
        from llama_cpp._utils import suppress_stdout_stderr

        return suppress_stdout_stderr(disable=False)
    except Exception:  # noqa: BLE001
        import contextlib

        return contextlib.nullcontext()


def _join(texts: list[str]) -> str:
    """几段的文本接起来。两段交界处都是英文单词 / 数字时补一个空格,中文之间不加。"""
    out = texts[0]
    for nxt in texts[1:]:
        if (
            out
            and nxt
            and out[-1].isascii()
            and out[-1].isalnum()
            and nxt[0].isascii()
            and nxt[0].isalnum()
        ):
            out += " "
        out += nxt
    return out
