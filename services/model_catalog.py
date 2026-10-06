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
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.i18n import EN, bi, localize
from shared.model_registry import IS_APPLE_SILICON, MODELS_CONFIG

logger = logging.getLogger("stt-server")

# 重建环境的命令。Windows 用户多半没有 bash,给 .sh 等于没给。
SETUP_COMMAND = r"scripts\setup-env.ps1" if sys.platform == "win32" else "scripts/setup-env.sh"

# 每种引擎需要能 import 的包。缺了就是「环境没装全」,而不是模型本身有问题。
_ENGINE_PACKAGES = {
    "qwen_asr_mlx_native": "mlx_audio",
    "whisper_mlx": "mlx_whisper",
    "whisper_turbo": "transformers",
    "qwen_asr_hf": "transformers",
}


def _package_version(name: str) -> tuple[int, ...] | None:
    """已装的包的版本(只取开头的数字段);没装或读不出来返回 None。"""
    try:
        from importlib import metadata

        parts: list[int] = []
        for piece in metadata.version(name).split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            if not digits or not piece[0].isdigit():
                break
            parts.append(int(digits))
        return tuple(parts) or None
    except Exception:  # noqa: BLE001 - 读不出版本就当不知道,不拦
        return None


def _has_package(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def unavailable_reason(info: dict[str, Any]) -> str | None:
    """这个模型在本机为什么用不了;能用时返回 None。

    返回 :class:`shared.i18n.Bilingual`(当 str 用就是中文),由 `describe` 按界面语言挑。
    """
    if info.get("requires_apple_silicon") and not IS_APPLE_SILICON:
        return bi("需要 Apple Silicon 的 Mac", "Requires an Apple Silicon Mac")
    engine = info.get("engine", "")
    if engine == "whisper_cpp":
        cli = Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli"
        if not cli.exists():
            return bi(
                "需要先自行编译 whisper.cpp(~/whisper.cpp)并下载模型",
                "Build whisper.cpp yourself first (~/whisper.cpp) and download the model",
            )
        return None
    package = _ENGINE_PACKAGES.get(engine)
    if package and not _has_package(package):
        return bi(
            f"环境里缺少 {package},请用 {SETUP_COMMAND} 重建环境",
            f"{package} is missing from the environment; rebuild it with {SETUP_COMMAND}",
        )
    if engine in ("whisper_turbo", "qwen_asr_hf") and not _has_package("torch"):
        return bi(
            f"环境里缺少 torch,请用 {SETUP_COMMAND} 重建环境",
            f"torch is missing from the environment; rebuild it with {SETUP_COMMAND}",
        )
    if engine == "qwen_asr_hf":
        from services.qwen_asr_hf import MIN_TRANSFORMERS

        have = _package_version("transformers")
        if have is not None and have[:2] < MIN_TRANSFORMERS:
            need = ".".join(map(str, MIN_TRANSFORMERS))
            got = ".".join(map(str, have))
            return bi(
                f"transformers 太旧({got}),这个模型要 {need} 以上;用 {SETUP_COMMAND} 重建环境",
                f"transformers is too old ({got}); this model needs {need}+. "
                f"Rebuild the environment with {SETUP_COMMAND}",
            )
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


def describe(name: str, lang: str = "zh") -> dict[str, Any]:
    """一个模型给界面看的全部信息(不含「当前是否已加载」这类运行时状态)。

    `lang` 是界面语言(见 shared/i18n.py):英文时用注册表里的 `description_en`。
    """
    info = MODELS_CONFIG[name]
    reason = unavailable_reason(info)
    description = info.get("description", name)
    if lang == EN:
        description = info.get("description_en", description)
    return {
        "description": description,
        "memory_gb": info.get("memory_gb"),
        "download_mb": info.get("download_mb"),
        "available": reason is None,
        "unavailable_reason": localize(lang, reason),
        "downloaded": is_downloaded(info),
        "recommended": name == recommended_model(),
    }


def cache_bytes(model_id: str) -> int:
    """这个模型在 HuggingFace 缓存里已经落盘的字节数(含下载中的 .incomplete)。

    用来给「首次加载要下载几百 MB 到几 GB」报进度。不挂 huggingface_hub 的进度
    回调,是因为几个引擎(mlx-audio / mlx-whisper / transformers / llama.cpp)各自调
    下载,接口和版本都不一样;数缓存目录的增长对谁都成立。

    数的是整个 `models--<org>--<name>` 目录里的**真实文件**,不只是 `blobs/`:Windows
    上没开开发者模式时建不了软链,huggingface_hub 下完一个文件就把它从 `blobs/`
    **挪**进 `snapshots/`——只数 `blobs/` 的话,每下完一个文件进度就倒退回去。
    软链不算(它指向的 blob 已经数过了)。
    """
    if "/" not in model_id:
        return 0
    root = _hf_cache_dir() / f"models--{model_id.replace('/', '--')}"
    total = 0
    for sub in ("blobs", "snapshots"):
        try:
            for dirpath, _dirs, files in os.walk(root / sub):
                for name in files:
                    path = os.path.join(dirpath, name)
                    try:
                        if not os.path.islink(path):
                            total += os.path.getsize(path)
                    except OSError:
                        pass
        except OSError:
            pass
    return total


# ── 下载了多少:接到 huggingface_hub 自己的进度上 ──
#
# 数缓存目录里文件的大小(`cache_bytes`)在新版 huggingface_hub(2.x,Xet 存储)上不灵了:
# 数据先从网络收下来攒着,隔很久才一大块一大块地拼进文件——实测一个 985 MB 的模型,网络上
# 已经收了 330 MB,缓存目录里还只有 5 MB;下完之后文件还会被挪进共享的 blob 目录,模型
# 自己的目录里只剩几个指针。新建的环境装到的就是新版,所以用户看到的是:网速跑得飞快,
# 界面上只有秒数在动,进度到 30% 才跳一下(2.7.1 在 Windows 上实测;和操作系统无关)。
#
# huggingface_hub 每收到一块数据都会调一次它那个 tqdm 进度条的 `update(n)`(不管进度条
# 显不显示;transformers、mlx-lm、mlx-audio、llama.cpp 这边的下载最后都走它)。在这个
# 类上包一层,把字节数累加起来,就是一个不依赖文件系统的计数。一个服务进程同一时间只
# 加载一个模型,所以加载开始时记一份读数,之后的差就是这次下载了多少。


class _DownloadMeter:
    """进程里 huggingface_hub 已经下载的字节数(只增不减),分两种记:

    - `written`:写进文件的字节。老版本的 huggingface_hub、普通 HTTP 下载只有这一种;
    - `transferred`:从网络收到的字节。新版本(Xet 存储)下载时**同时**报两条进度——
      「downloading bytes」是网络上收到的(压缩、去重过,通常比文件小),「reconstructing
      file」是已经拼好写进文件的。后者一大块一大块地跳,前者才是「网速在跑」的那个数。

    两条不能加在一起(那样会数出文件大小的 1.5~1.9 倍,CI 上实测),也不能只看一条:
    只看写入的,进度很久才跳一次;只看收到的,下完了也到不了 100%。取大的那个:
    下载过程中跟着网络走,最后落在文件的真实大小上,而且不会超过它。
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._written = 0
        self._transferred = 0
        self.installed = False

    def add(self, n: float, transfer: bool = False) -> None:
        with self._lock:
            if transfer:
                self._transferred += int(n)
            else:
                self._written += int(n)

    def snapshot(self) -> tuple[int, int]:
        """(写进文件的, 从网络收到的)。加载开始时记一份,之后用 `since` 算差。"""
        return self._written, self._transferred

    def since(self, start: tuple[int, int]) -> int:
        """从 `start` 那一刻到现在下载了多少字节(两种计数各自的增量里大的那个)。"""
        written, transferred = self.snapshot()
        return max(0, written - start[0], transferred - start[1])


DOWNLOAD_METER = _DownloadMeter()

#: 新版 huggingface_hub 里「从网络收到的字节」那条进度条的名字(单个文件是
#: `<文件名>: downloading bytes`,整仓库下载是 `Downloading bytes`)。
_TRANSFER_BAR_MARK = "downloading bytes"


def install_download_meter() -> bool:
    """给 huggingface_hub 的进度条装上计数(只装一次)。装不上就返回 False,那时只能数文件。

    只数按字节计的进度条(`unit="B"`):`snapshot_download` 还有一个「Fetching N files」
    的按个数计的,不能算进去。`unit` 和名字要在构造时记下来——进度条被关掉显示
    (`HF_HUB_DISABLE_PROGRESS_BARS`)时 tqdm 不保存这些属性。
    """
    if DOWNLOAD_METER.installed:
        return True
    try:
        from huggingface_hub.utils import tqdm as hf_tqdm

        # 标记打在类上:哪怕这个模块被重新加载过,也不会在同一个类上包两层(那样会数两遍)。
        if getattr(hf_tqdm, "_vif_metered", False):
            DOWNLOAD_METER.installed = True
            return True
        original_init = hf_tqdm.__init__
        original_update = hf_tqdm.update

        def __init__(self, *args, **kwargs):
            self._vif_bytes = kwargs.get("unit") == "B"
            self._vif_transfer = _TRANSFER_BAR_MARK in str(kwargs.get("desc") or "").lower()
            original_init(self, *args, **kwargs)

        def update(self, n=1):
            if n and getattr(self, "_vif_bytes", False):
                DOWNLOAD_METER.add(n, transfer=getattr(self, "_vif_transfer", False))
            return original_update(self, n)

        hf_tqdm.__init__ = __init__
        hf_tqdm.update = update
        hf_tqdm._vif_metered = True
        DOWNLOAD_METER.installed = True
        return True
    except Exception as e:  # noqa: BLE001 - 进度只是锦上添花,装不上不该影响加载
        logger.debug(f"download meter unavailable: {e}")
        return False


def load_progress(
    model: str,
    cached: int,
    started_at: float,
    bytes_at_start: int,
    download_mb: float | None,
    now: float,
    metered: int = 0,
) -> dict[str, Any]:
    """`/health.loading` 的内容:加载了多久、下载了多少、一共要下多少。STT / LLM 共用。

    「下载了多少」有两个来源,取大的那个:

    - `cached - bytes_at_start`:缓存目录比加载开始时多出来的字节(`cache_bytes` 量的)。
      新版 huggingface_hub 上它很久才动一下(见上面 `_DownloadMeter` 前的说明);
    - `metered`:加载开始以来 huggingface_hub 的进度回调累计的字节(`DOWNLOAD_METER.since()`)。
      不依赖文件系统;计数没装上时是 0,退回上面那个。

    返回的字段:

    - `downloaded_bytes`:**这次加载**新下载的字节数(老客户端只认它);
    - `cached_bytes`:本地现在一共有多少(上次下到一半、这次接着下时比上面那个大);
    - `total_bytes`:下完一共多大(注册表里的 `download_mb`),不知道时为 None。
      只是个估计:仓库更新过、或者引擎多拉了几个文件时会对不上,所以界面上写「约」,
      超过了就不再显示分母。
    """
    downloaded = max(0, cached - bytes_at_start, metered)
    return {
        "model": model,
        "elapsed_s": round(now - started_at, 1),
        "downloaded_bytes": downloaded,
        # 缓存目录的大小跟不上时(新版 huggingface_hub)用计数顶上
        "cached_bytes": max(cached, metered),
        # download_mb 是十进制的 MB(HuggingFace 报的字节数 / 1e6)
        "total_bytes": int(download_mb * 1_000_000) if download_mb else None,
        "phase": "downloading" if downloaded > 0 else "loading",
    }
