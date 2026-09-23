"""模型目录:给界面看的模型信息(说人话的描述、能不能在这台机器上跑、下没下载、推荐哪个)。

以前 `/models` 每一项的描述都是 "STT model: <model_id>",界面上只显示内部名
`qwen_asr_mlx_native_small`;注册表里写好的中文描述和内存占用没人用。更糟的是
所有模型对所有人一视同仁地列出来:Linux / Windows 上照样列着只能在 Apple Silicon
上跑的 MLX 模型,whisper.cpp 那两个要用户自己在 ~/whisper.cpp 下编译、手动下权重,
普通用户选了只会失败。
"""

from __future__ import annotations

import importlib.util
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.model_registry import IS_APPLE_SILICON, MODELS_CONFIG

logger = logging.getLogger("stt-server")

# 每种引擎需要能 import 的包。缺了就是「环境没装全」,而不是模型本身有问题。
_ENGINE_PACKAGES = {
    "qwen_asr_mlx_native": "mlx_audio",
    "whisper_mlx": "mlx_whisper",
    "whisper_turbo": "transformers",
}


def _has_package(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def unavailable_reason(info: dict[str, Any]) -> str | None:
    """这个模型在本机为什么用不了;能用时返回 None。"""
    if info.get("requires_apple_silicon") and not IS_APPLE_SILICON:
        return "需要 Apple Silicon 的 Mac"
    engine = info.get("engine", "")
    if engine == "whisper_cpp":
        cli = Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli"
        if not cli.exists():
            return "需要先自行编译 whisper.cpp(~/whisper.cpp)并下载模型"
        return None
    package = _ENGINE_PACKAGES.get(engine)
    if package and not _has_package(package):
        return f"环境里缺少 {package},请用 scripts/setup-env.sh 重建环境"
    if engine == "whisper_turbo" and not _has_package("torch"):
        return "环境里缺少 torch,请用 scripts/setup-env.sh 重建环境"
    return None


def _hf_cache_dir() -> Path:
    hub = os.getenv("HF_HUB_CACHE")
    if hub:
        return Path(hub)
    home = os.getenv("HF_HOME")
    base = Path(home) if home else Path.home() / ".cache" / "huggingface"
    return base / "hub"


def is_downloaded(info: dict[str, Any]) -> bool | None:
    """模型权重是否已经在本地缓存里。判断不了(不是 HuggingFace 模型)时返回 None。"""
    model_id = info.get("model_id", "")
    if "/" not in model_id:
        return None
    snapshots = _hf_cache_dir() / f"models--{model_id.replace('/', '--')}" / "snapshots"
    try:
        return any(snapshots.iterdir())
    except OSError:
        return False


@lru_cache(maxsize=1)
def recommended_model() -> str | None:
    """按本机硬件推荐的模型(探测一次就缓存)。探测不了时返回 None。"""
    try:
        from services.device import profile, recommend_stt_model

        return recommend_stt_model(profile())[0]
    except Exception as e:  # noqa: BLE001 - 推荐只是锦上添花,探测失败不该影响列表
        logger.debug(f"hardware recommendation unavailable: {e}")
        return None


def describe(name: str) -> dict[str, Any]:
    """一个模型给界面看的全部信息(不含「当前是否已加载」这类运行时状态)。"""
    info = MODELS_CONFIG[name]
    reason = unavailable_reason(info)
    return {
        "description": info.get("description", name),
        "memory_gb": info.get("memory_gb"),
        "available": reason is None,
        "unavailable_reason": reason,
        "downloaded": is_downloaded(info),
        "recommended": name == recommended_model(),
    }


def cache_bytes(model_id: str) -> int:
    """这个模型在 HuggingFace 缓存里已经落盘的字节数(含下载中的 .incomplete)。

    用来给「首次加载要下载几百 MB 到几 GB」报进度。不挂 huggingface_hub 的进度
    回调,是因为几个引擎(mlx-audio / mlx-whisper / transformers)各自调下载,
    接口和版本都不一样;数缓存目录的增长对谁都成立。
    """
    if "/" not in model_id:
        return 0
    blobs = _hf_cache_dir() / f"models--{model_id.replace('/', '--')}" / "blobs"
    total = 0
    try:
        for f in blobs.iterdir():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        return 0
    return total
