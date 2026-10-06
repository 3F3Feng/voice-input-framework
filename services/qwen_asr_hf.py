"""Qwen3-ASR 的 transformers 引擎(Windows / Linux / 任何有 PyTorch 的机器)。

非 Apple 平台以前只有 Whisper,中文明显不如 Qwen3-ASR;Qwen3-ASR 只有 MLX 版
(`qwen_asr_mlx_native*`),只能在 Apple Silicon 上跑。早先(PR #6)想接 `qwen-asr`
这个包,它把 transformers 钉死在 4.57.6,还拖着 gradio / flask 一串依赖。
transformers 5.13 起原生支持 Qwen3-ASR(`Qwen/Qwen3-ASR-*-hf`),不需要那个包了:
CPU、CUDA、ROCm、MPS 走的是同一段代码,区别只在设备和精度(见 services/device.py)。

模型相关的东西都收在这个类里;`services/stt_engine.py` 只管在模型线程上调它。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("stt-server")

#: 这个引擎要的最低 transformers 版本(原生 Qwen3-ASR 是 5.13.0 加进去的)。
MIN_TRANSFORMERS = (5, 13)

#: 生成长度上限:每秒音频给 15 个 token(中文连续说话实测每秒 4–6 个 token,
#: 英文快语速 8 个左右),再加一点起步量。给少了长句会被截断,给多了没有代价
#: ——模型说完会自己停。
TOKENS_PER_SECOND = 15
MIN_NEW_TOKENS = 128
MAX_NEW_TOKENS = 4096


def token_budget(seconds: float) -> int:
    return int(min(MAX_NEW_TOKENS, MIN_NEW_TOKENS + max(0.0, seconds) * TOKENS_PER_SECOND))


def pick_dtype_name(backend_name: str, backend_dtype: str) -> str:
    """这个引擎在这种后端上用什么精度。

    GPU 上用后端挑好的半精度。CPU 上一律 float32:`services/device.py` 在支持
    AVX512-BF16 的 CPU 上会给 bfloat16(Whisper 那条 pipeline 验证过),这个模型在
    CPU bf16 上没实测过,宁可慢一点。
    """
    return "float32" if backend_name == "cpu" else backend_dtype


class QwenASRTransformers:
    """加载好的 Qwen3-ASR(transformers)。加载和推理都必须在同一个线程上调。"""

    def __init__(self, model_id: str, device: str, dtype_name: str):
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        self._torch = torch
        dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}[
            dtype_name
        ]
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForMultimodalLM.from_pretrained(model_id, dtype=dtype).to(device)
        self.model.eval()

    def transcribe(
        self,
        audio,
        sample_rate: int,
        language: str | None,
        context: str | None = None,
    ) -> tuple[str, str | None]:
        """转写一段 16 kHz 的 float32 音频,返回 (文本, 模型报的语言名)。

        `language` 是 Qwen 的语言名或代码("Chinese" / "zh"),None = 自动检测;
        指定了语言时模型不再报语言,第二项为 None。`context` 是个人词库的热词,
        当系统提示传进去。
        """
        request: dict[str, Any] = {"audio": audio}
        if language:
            request["language"] = language
        if context:
            request["prompt"] = context
        inputs = self.processor.apply_transcription_request(**request).to(
            self.model.device, self.model.dtype
        )
        with self._torch.inference_mode():
            output_ids = self.model.generate(
                **inputs, max_new_tokens=token_budget(len(audio) / max(1, sample_rate))
            )
        generated = output_ids[:, inputs["input_ids"].shape[1] :]
        parsed = self.processor.decode(generated, return_format="parsed")[0]
        return (parsed.get("transcription") or "").strip(), parsed.get("language")
