#!/usr/bin/env python3
"""选一个推理后端:用哪个设备、用什么精度。

以前这件事是散在 `stt_engine.py` 和 `diarize_engine.py` 里的三行 if:

    if torch.backends.mps.is_available():   device = "mps"
    elif torch.cuda.is_available():         device = "cuda"
    else:                                   device = "cpu"

够用,但漏了几件真实存在的事:

* **AMD(ROCm)认不出来。** ROCm 版的 PyTorch 会让 `torch.cuda.is_available()`
  返回 True、设备名也叫 "cuda" —— 所以 Linux + ROCm 其实是**碰巧**能跑的,
  但日志里会报成 "cuda",出了问题没人能从日志看出这是张 A 卡。用
  `torch.version.hip` 才分得清。
* **Intel 独显 / Arc(XPU)完全没覆盖。** `torch.xpu` 压根没被问过。
* **精度是照着设备硬编码的**:`float16 if device == "cuda" else float32`。
  于是 MPS 跑在 fp32 上 —— Apple 的 GPU 支持 fp16,这是白扔的一半带宽。

这里把判断集中到一处,顺带把「到底选中了什么」讲清楚,好让 /health 和界面
如实显示,而不是让用户猜。

环境变量可以强制覆盖(排查问题时有用):
    VIF_DEVICE=cpu|cuda|mps|xpu     强制设备
    VIF_DTYPE=float16|bfloat16|float32   强制精度
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("device")


@dataclass(frozen=True)
class Backend:
    """选定的后端。`torch_device` 才是给 torch 的,`name` 只用来说人话。"""

    name: str
    torch_device: str
    dtype_name: str
    detail: str

    @property
    def is_gpu(self) -> bool:
        return self.torch_device != "cpu"

    def torch_dtype(self):
        import torch

        return {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }[self.dtype_name]

    def as_dict(self) -> dict:
        return {
            "backend": self.name,
            "device": self.torch_device,
            "dtype": self.dtype_name,
            "detail": self.detail,
        }


def _cpu_supports_bf16(torch) -> bool:
    """这颗 CPU 上 bf16 是不是真的更快。

    只认硬件有 AVX512-BF16 / AMX 的情况。没有硬件支持时 bf16 要靠软件模拟,
    比 fp32 还慢 —— 那就不是优化,是帮倒忙。
    """
    for probe in ("_is_avx512_bf16_supported", "_is_amx_tile_supported"):
        fn = getattr(getattr(torch, "cpu", None), probe, None)
        try:
            if callable(fn) and fn():
                return True
        except Exception:  # noqa: BLE001 - 探测函数在部分构建里会抛
            pass
    return False


def detect(torch=None) -> Backend:
    """挑一个后端。`torch` 参数只为测试注入,正常调用不要传。"""
    if torch is None:
        import torch  # noqa: PLC0415

    forced_device = (os.getenv("VIF_DEVICE") or "").strip().lower()
    forced_dtype = (os.getenv("VIF_DTYPE") or "").strip().lower()

    backend = _detect_auto(torch) if not forced_device else _forced(torch, forced_device)

    if forced_dtype in ("float16", "bfloat16", "float32"):
        backend = Backend(
            backend.name,
            backend.torch_device,
            forced_dtype,
            f"{backend.detail}(精度被 VIF_DTYPE 指定为 {forced_dtype})",
        )
    return backend


def _forced(torch, device: str) -> Backend:
    dtype = "float16" if device in ("cuda", "mps", "xpu") else "float32"
    return Backend(device, device, dtype, f"由 VIF_DEVICE 指定为 {device}")


def _detect_auto(torch) -> Backend:
    # ── Apple Silicon ──
    # fp16 而不是 fp32:MPS 支持半精度,Whisper 一类模型用 fp16 显存和带宽都减半,
    # 精度损失可以忽略。以前这里一律 fp32,纯属浪费。
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return Backend("mps", "mps", "float16", "Apple Silicon GPU (Metal)")

    # ── AMD ROCm ──
    # 必须排在 CUDA 前面:ROCm 版 torch 的 `cuda.is_available()` 也是 True,
    # 先问 CUDA 的话 A 卡会被一路报成 N 卡。
    if torch.cuda.is_available() and getattr(torch.version, "hip", None):
        name = _gpu_name(torch)
        return Backend("rocm", "cuda", "float16", f"AMD GPU (ROCm {torch.version.hip}){name}")

    # ── NVIDIA CUDA ──
    if torch.cuda.is_available():
        name = _gpu_name(torch)
        # Ampere(SM80)及以后 bf16 是原生的,数值范围比 fp16 稳,不易出 NaN
        dtype = "float16"
        try:
            major, _ = torch.cuda.get_device_capability(0)
            if major >= 8:
                dtype = "bfloat16"
        except Exception:  # noqa: BLE001
            pass
        return Backend("cuda", "cuda", dtype, f"NVIDIA GPU (CUDA {torch.version.cuda}){name}")

    # ── Intel 独显 / Arc ──
    xpu = getattr(torch, "xpu", None)
    if xpu is not None:
        try:
            if xpu.is_available():
                return Backend("xpu", "xpu", "float16", "Intel GPU (XPU)")
        except Exception:  # noqa: BLE001
            pass

    # ── CPU ──
    dtype = "bfloat16" if _cpu_supports_bf16(torch) else "float32"
    threads = getattr(torch, "get_num_threads", lambda: 0)()
    return Backend("cpu", "cpu", dtype, f"CPU（{threads} 线程）")


def _gpu_name(torch) -> str:
    try:
        return f" - {torch.cuda.get_device_name(0)}"
    except Exception:  # noqa: BLE001
        return ""


# ── 机器画像与模型推荐 ─────────────────────────────────────────────────────
#
# 光挑设备不够。默认模型如果配不上这台机器,用户开箱看到的要么是「怎么这么慢」,
# 要么直接 OOM。所以要同时看两件事:**算得动吗**、**装得下吗**。
#
# 下面那几个门槛不是拍脑袋定的,是在一台 Intel i5-9400(6 核 / 8GB / 无独显)
# 上实测出来的(2.7 秒英文,transformers whisper,CPU fp32):
#
#     whisper_tiny    加载  5.1s   转写 1.0s   实时率 0.37x
#     whisper_base    加载 11.7s   转写 1.9s   实时率 0.70x
#     whisper_small   加载 24.1s   转写 6.2s   实时率 2.31x   ← 比说话还慢
#
# 实时率大于 1 就意味着「说 10 秒要等 20 秒」,那不叫能用。6 核跑 small 是
# 2.31x,按核数线性外推,要压到 1x 以下大约需要 14 核往上 —— 所以 small 的
# CPU 门槛定在 16 核。

import shutil  # noqa: E402

#: 纯 CPU 推理:核数门槛 → 模型。从大到小匹配,取第一个够格的。
_CPU_SPEED_TIERS: list[tuple[int, str]] = [
    (16, "whisper_small"),
    (4, "whisper_base"),
    (0, "whisper_tiny"),
]

#: GPU 推理:按显存挑,越大越好。(所需显存 GB, 模型)
_GPU_VRAM_TIERS: list[tuple[float, str]] = [
    (6.0, "whisper_turbo"),
    (4.0, "whisper_medium"),
    (2.0, "whisper_small"),
    (1.0, "whisper_base"),
    (0.0, "whisper_tiny"),
]

#: Apple Silicon:统一内存。MLX 模型本身很省,门槛主要防的是小内存机型。
_APPLE_RAM_TIERS: list[tuple[float, str]] = [
    (16.0, "qwen_asr_mlx_native"),
    (0.0, "qwen_asr_mlx_native_small"),
]


@dataclass(frozen=True)
class MachineProfile:
    """这台机器能吃多大的模型。"""

    backend: Backend
    cpu_cores: int
    ram_gb: float
    #: 独立显存;统一内存架构(Apple)和纯 CPU 下为 None
    vram_gb: float | None

    def as_dict(self) -> dict:
        d = self.backend.as_dict()
        d.update(
            cpu_cores=self.cpu_cores,
            ram_gb=round(self.ram_gb, 1),
            vram_gb=round(self.vram_gb, 1) if self.vram_gb else None,
        )
        return d


def _total_ram_gb() -> float:
    """物理内存总量(GB)。拿不到就返回 0,调用方按「不知道」处理。"""
    try:  # Linux / macOS
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
    except (ValueError, OSError, AttributeError):
        pass
    try:  # Windows
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys / (1024**3)
    except Exception:  # noqa: BLE001
        return 0.0


def _vram_gb(torch, backend: Backend) -> float | None:
    """独显显存。Apple 是统一内存,归到 ram 里,这里返回 None。"""
    if backend.name in ("cuda", "rocm"):
        try:
            return torch.cuda.get_device_properties(0).total_memory / (1024**3)
        except Exception:  # noqa: BLE001
            return None
    if backend.name == "xpu":
        try:
            return torch.xpu.get_device_properties(0).total_memory / (1024**3)
        except Exception:  # noqa: BLE001
            return None
    return None


def profile(torch=None) -> MachineProfile:
    """把这台机器的后端 / 核数 / 内存 / 显存量出来。"""
    if torch is None:
        import torch  # noqa: PLC0415

    backend = detect(torch)
    # 物理核。逻辑核(超线程)对这类计算密集推理帮助有限,按物理核估更接近实际。
    cores = os.cpu_count() or 1
    try:
        cores = len(os.sched_getaffinity(0))  # 容器里被限核时这个才准
    except AttributeError:
        pass
    return MachineProfile(backend, cores, _total_ram_gb(), _vram_gb(torch, backend))


def recommend_stt_model(p: MachineProfile) -> tuple[str, str]:
    """给这台机器挑一个 STT 模型,返回(模型名, 为什么)。"""
    # ── Apple Silicon:统一内存,用 MLX ──
    if p.backend.name == "mps":
        for need, model in _APPLE_RAM_TIERS:
            if p.ram_gb >= need:
                return model, f"Apple Silicon,统一内存 {p.ram_gb:.0f}GB"
    # ── 独显:按显存挑,并留 30% 余量给激活值和别的程序 ──
    if p.vram_gb:
        budget = p.vram_gb * 0.7
        for need, model in _GPU_VRAM_TIERS:
            if budget >= need:
                return model, (
                    f"{p.backend.name.upper()} 显存 {p.vram_gb:.1f}GB(按 {budget:.1f}GB 可用估)"
                )
    # ── 纯 CPU:算力和内存两个约束都要满足,取更紧的那个 ──
    by_speed = next(m for need, m in _CPU_SPEED_TIERS if p.cpu_cores >= need)
    by_memory = _cpu_model_fitting_ram(p.ram_gb)
    chosen = _smaller_of(by_speed, by_memory)
    why = f"CPU {p.cpu_cores} 核 / 内存 {p.ram_gb:.0f}GB"
    if chosen != by_speed:
        why += f";内存不够跑 {by_speed},降到 {chosen}"
    return chosen, why


#: CPU 上跑 fp32,权重大约是 fp16 估值的两倍,再给运行时留 2GB。
_CPU_MODEL_RAM_NEED: list[tuple[float, str]] = [
    (8.0, "whisper_small"),
    (4.0, "whisper_base"),
    (0.0, "whisper_tiny"),
]

_SIZE_ORDER = ["whisper_tiny", "whisper_base", "whisper_small", "whisper_medium", "whisper_turbo"]


def _cpu_model_fitting_ram(ram_gb: float) -> str:
    if ram_gb <= 0:  # 量不出来就按最保守的来
        return "whisper_tiny"
    return next(m for need, m in _CPU_MODEL_RAM_NEED if ram_gb >= need)


def _smaller_of(a: str, b: str) -> str:
    try:
        return a if _SIZE_ORDER.index(a) <= _SIZE_ORDER.index(b) else b
    except ValueError:
        return a


def describe() -> str:
    """一行人话,给启动日志用。"""
    p = profile()
    model, why = recommend_stt_model(p)
    disk = shutil.disk_usage(os.path.expanduser("~")).free / (1024**3)
    return (
        f"{p.backend.detail} | {p.cpu_cores} 核 | 内存 {p.ram_gb:.0f}GB"
        + (f" | 显存 {p.vram_gb:.1f}GB" if p.vram_gb else "")
        + f" | 磁盘可用 {disk:.0f}GB → 建议模型 {model}({why})"
    )
