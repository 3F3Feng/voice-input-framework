"""探测这台机器,算出该用哪一套配置(规则见 shared/hardware_plan.py)。

显卡看的是 **llama.cpp 认得出什么**,而不是 PyTorch:非 Apple 平台上识别(量化版
Qwen3-ASR)和后处理(GGUF 的 LLM)都跑在 llama.cpp 上,它在 NVIDIA 上走 CUDA,在
AMD / Intel 上走 Vulkan——PyTorch 在 Windows 的 A 卡上根本没有显卡版,问它只会得到
「没有显卡」。直接问 llama.cpp 的设备表(名字、类型、显存),问到的就是模型真正会用的
那块显卡;装的是 CPU 版、驱动不可用时表里没有显卡,配置也就按没有显卡来选。

STT 和 LLM 两个服务各自调这里,看到的是同一台机器,算出来的是同一套。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

from shared import llama_runtime, llm_backend
from shared.hardware_plan import Machine, Plan, plan
from shared.model_registry import IS_APPLE_SILICON

logger = logging.getLogger(__name__)

#: ggml 的设备类型(ggml-backend.h 的 enum ggml_backend_dev_type)。
DEVICE_CPU, DEVICE_GPU, DEVICE_IGPU, DEVICE_ACCEL = 0, 1, 2, 3

GB = 1024**3


def gpu_kind(device_name: str) -> str:
    """ggml 的设备名(`CUDA0` / `Vulkan0` / `MTL0`)→ 后端名。"""
    name = device_name.lower()
    for prefix, kind in (
        ("cuda", "cuda"),
        ("vulkan", "vulkan"),
        ("mtl", "metal"),
        ("metal", "metal"),
    ):
        if name.startswith(prefix):
            return kind
    return name.rstrip("0123456789") or "gpu"


def pick_gpu(devices: list[dict]) -> dict | None:
    """设备表里的独立显卡,有好几块时取显存最大的。集成显卡不算——它用的是系统内存,

    跑 Vulkan 也不比 CPU 快。
    """
    gpus = [d for d in devices if d.get("type") == DEVICE_GPU]
    return max(gpus, key=lambda d: d.get("total_bytes", 0)) if gpus else None


def _ggml_library() -> ctypes.CDLL:
    """llama-cpp-python 自带的 ggml 动态库(设备表在这里面)。"""
    import llama_cpp

    lib_dir = Path(llama_cpp.__file__).parent / "lib"
    names = {"win32": ["ggml.dll"], "darwin": ["libggml.dylib"]}.get(sys.platform, ["libggml.so"])
    for name in names:
        path = lib_dir / name
        if path.exists():
            return ctypes.CDLL(str(path))
    raise FileNotFoundError(f"no ggml library in {lib_dir}")


def llama_devices() -> list[dict]:
    """llama.cpp 的设备表:`[{name, description, type, total_bytes, free_bytes}]`。

    没装 llama.cpp、库加载不了时返回空表(当成没有显卡)。
    """
    if not llm_backend.has_package("llama_cpp"):
        return []
    try:
        # CUDA 版要的 CUDA 运行库在 PyTorch 的目录里,得先指给它,不然 import 就失败。
        llama_runtime.prepare()
        import llama_cpp

        llama_cpp.llama_backend_init()
        ggml = _ggml_library()
        ggml.ggml_backend_dev_count.restype = ctypes.c_size_t
        ggml.ggml_backend_dev_get.restype = ctypes.c_void_p
        ggml.ggml_backend_dev_get.argtypes = [ctypes.c_size_t]
        for fn in (ggml.ggml_backend_dev_name, ggml.ggml_backend_dev_description):
            fn.restype = ctypes.c_char_p
            fn.argtypes = [ctypes.c_void_p]
        ggml.ggml_backend_dev_type.restype = ctypes.c_int
        ggml.ggml_backend_dev_type.argtypes = [ctypes.c_void_p]
        ggml.ggml_backend_dev_memory.restype = None
        ggml.ggml_backend_dev_memory.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        devices = []
        for i in range(ggml.ggml_backend_dev_count()):
            dev = ggml.ggml_backend_dev_get(i)
            free, total = ctypes.c_size_t(), ctypes.c_size_t()
            ggml.ggml_backend_dev_memory(dev, ctypes.byref(free), ctypes.byref(total))
            devices.append(
                {
                    "name": (ggml.ggml_backend_dev_name(dev) or b"").decode(errors="replace"),
                    "description": (ggml.ggml_backend_dev_description(dev) or b"").decode(
                        errors="replace"
                    ),
                    "type": int(ggml.ggml_backend_dev_type(dev)),
                    "total_bytes": int(total.value),
                    "free_bytes": int(free.value),
                }
            )
        return devices
    except Exception as e:  # noqa: BLE001 - 探测失败只意味着按没有显卡来选,不该让服务起不来
        logger.warning(f"could not list llama.cpp devices: {e}")
        return []


@lru_cache(maxsize=1)
def machine() -> Machine:
    """这台机器的画像(探测一次就缓存)。"""
    from services.device import total_ram_gb

    ram = total_ram_gb()
    cores = os.cpu_count() or 1
    if IS_APPLE_SILICON:
        return Machine(apple_silicon=True, gpu="metal", ram_gb=ram, cores=cores)
    has_llama = llm_backend.has_package("llama_cpp")
    gpu = pick_gpu(llama_devices()) if has_llama else None
    if gpu is None:
        return Machine(ram_gb=ram, cores=cores, llama_cpp=has_llama)
    return Machine(
        gpu=gpu_kind(gpu["name"]),
        gpu_name=gpu["description"] or gpu["name"],
        vram_gb=gpu["total_bytes"] / GB,
        ram_gb=ram,
        cores=cores,
        llama_cpp=True,
    )


@lru_cache(maxsize=1)
def current_plan() -> Plan:
    """这台机器的那一套配置。"""
    chosen = plan(machine())
    logger.info(f"hardware plan: {chosen.tier} ({chosen.why})")
    return chosen


def recommended_stt_model() -> tuple[str, str]:
    """这台机器默认用哪个识别模型,以及为什么。

    按配置表来;表里选的模型在这个环境里用不了(老环境没装 llama.cpp)时,退回以前
    按 PyTorch 看到的硬件挑 Whisper 的办法——那样有显卡的老环境至少还能用上显卡。
    """
    from services import model_catalog
    from shared.i18n import localize
    from shared.model_registry import MODELS_CONFIG

    chosen = current_plan()
    info = MODELS_CONFIG.get(chosen.stt_model)
    if info is not None and model_catalog.unavailable_reason(info) is None:
        return chosen.stt_model, localize("zh", chosen.why)
    from services.device import profile, recommend_stt_model

    return recommend_stt_model(profile())
