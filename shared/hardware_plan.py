"""这台机器该用哪一套:识别模型、后处理模型、放在显卡上还是 CPU 上。

以前各选各的:STT 服务按 PyTorch 看到的显存挑 Whisper,LLM 服务不看硬件一律用同一个
模型,两边谁也不知道对方占了多少显存。结果是 8 GB 的显卡上两个模型抢显存,没有显卡的
机器上后处理默认开着、一句话要等好几秒。

这里把「机器画像 → 一套配置」写成一张表,两个服务都按它来(各自探测到的是同一台
机器,算出来的是同一套):

==================  ==========================  =====================  ======================
硬件                档位                        语音识别               后处理(LLM)
==================  ==========================  =====================  ======================
Apple 芯片(MLX)   内存 ≥ 16 GB                Qwen3-ASR-1.7B 8bit    Gemma-4-E4B
                    内存 < 16 GB                Qwen3-ASR-0.6B 4bit    Gemma-4-E2B
独立显卡            显存 ≥ 11 GB                Qwen3-ASR-1.7B Q8      Gemma-4-E4B(显卡)
(NVIDIA 走 CUDA;  显存 5.5–11 GB              Qwen3-ASR-0.6B Q8      Gemma-4-E2B(显卡)
AMD / Intel Arc     显存 2.5–5.5 GB             Qwen3-ASR-0.6B Q8      Gemma-4-E2B(CPU)
走 Vulkan)
没有独立显卡        ≥ 6 线程 且 内存 ≥ 8 GB     Qwen3-ASR-0.6B Q8      Gemma-4-E2B(CPU)
                    更小的机器                  Whisper Base / Tiny    Gemma-4-E2B(CPU)
==================  ==========================  =====================  ======================

后处理跑在 CPU 上时一句话要等好几秒:只有 ≥ 12 线程且内存 ≥ 16 GB 的机器默认开着,其余
默认关(用户可以自己打开)。「线程」是系统报的逻辑处理器数(4 核 8 线程算 8)。

显存的门槛是按「两个模型都放得下,还给桌面留一截」算的(Windows 的桌面自己要占
0.5–1.5 GB):0.6B Q8 约 1.3 GB、1.7B Q8 约 3 GB、E2B 约 3.8 GB、E4B 约 6 GB。
8 GB 的显卡因此落在第二档(1.3 + 3.8 = 5.1 GB),而不是勉强塞进 1.7B。

哪些是实测过的、哪些是按规格推的,见 docs/UX_REVIEW.md 5.2。这个模块是纯函数,
不碰硬件;探测在 services/hardware.py。
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.i18n import bi

# 模型名(和 shared/model_registry.py、services/llm_server.py 里的一致)
STT_MLX_LARGE = "qwen_asr_mlx_native"
STT_MLX_SMALL = "qwen_asr_mlx_native_small"
STT_QWEN_LARGE = "qwen_asr"
STT_QWEN_SMALL = "qwen_asr_small"
STT_WHISPER_BASE = "whisper_base"
STT_WHISPER_TINY = "whisper_tiny"

LLM_MLX_LARGE = "Gemma-4-E4B"
LLM_MLX_SMALL = "Gemma-4-E2B"
LLM_GGUF_LARGE = "Gemma-4-E4B-GGUF"
LLM_GGUF_SMALL = "Gemma-4-E2B-GGUF"

#: 16 GB 的 Mac 报出来可能略少于 16,门槛放在 15。
APPLE_LARGE_RAM_GB = 15.0
#: 独立显卡的三档(显存总量,GB)。
GPU_LARGE_VRAM_GB = 11.0
GPU_BOTH_VRAM_GB = 5.5
GPU_MIN_VRAM_GB = 2.5
#: 没有显卡时跑量化版 Qwen3-ASR 的门槛(`cores` 是逻辑处理器数:4 核 8 线程、6 核 6 线程
#: 都够)。本机(M3 Max)只给 2 个线程实测:5 秒的话 0.9 秒、90 秒的话 11.5 秒出结果;
#: 老一些的 x86 核心慢两三倍,仍然远快于说话的速度。
CPU_QWEN_MIN_CORES = 6
CPU_QWEN_MIN_RAM_GB = 7.5
#: 后处理跑在 CPU 上还默认开着的门槛(6 核 12 线程 / 8 核起)。
CPU_LLM_ON_MIN_CORES = 12
CPU_LLM_ON_MIN_RAM_GB = 15.0
CPU_BASE_MIN_CORES = 4
CPU_BASE_MIN_RAM_GB = 3.5


@dataclass(frozen=True)
class Machine:
    """机器画像。`gpu` 是 llama.cpp 实际能用的显卡后端,不是「装了什么显卡」:

    显卡在、但装的是 CPU 版的 llama.cpp(或者驱动不可用)时这里是 None——模型到时候
    只能跑在 CPU 上,配置就该按没有显卡来选。
    """

    apple_silicon: bool = False
    #: "cuda" / "vulkan" / "metal";没有可用的显卡时为 None。
    gpu: str | None = None
    gpu_name: str = ""
    #: 显存总量(GB)。Apple 芯片是统一内存,这里不用。
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    #: 逻辑处理器数(`os.cpu_count()`)。
    cores: int = 1
    #: 环境里有没有 llama.cpp。没有的话量化版 Qwen3-ASR 和 GGUF 的 LLM 都用不了。
    llama_cpp: bool = True


@dataclass(frozen=True)
class Plan:
    #: 档位的名字,日志和诊断信息里用(`apple-large` / `gpu-both` / `cpu-qwen` ……)。
    tier: str
    stt_model: str
    llm_model: str
    #: 后处理模型放不放显卡上。False 时 LLM 服务不往显卡上放一层(留给识别模型)。
    llm_on_gpu: bool
    #: 用户没有明确开关过时,后处理默认开不开。
    llm_default_on: bool
    #: 为什么是这一档(中英文)。
    why: str

    def as_dict(self, lang: str = "zh") -> dict:
        from shared.i18n import localize

        return {
            "tier": self.tier,
            "stt_model": self.stt_model,
            "llm_model": self.llm_model,
            "llm_on_gpu": self.llm_on_gpu,
            "llm_default_on": self.llm_default_on,
            "why": localize(lang, self.why),
        }


def _cpu_stt(m: Machine) -> str:
    """没有显卡(或者显卡太小)时的识别模型。"""
    if m.llama_cpp and m.cores >= CPU_QWEN_MIN_CORES and m.ram_gb >= CPU_QWEN_MIN_RAM_GB:
        return STT_QWEN_SMALL
    if m.cores >= CPU_BASE_MIN_CORES and m.ram_gb >= CPU_BASE_MIN_RAM_GB:
        return STT_WHISPER_BASE
    return STT_WHISPER_TINY


def _cpu_llm_on(m: Machine) -> bool:
    return m.cores >= CPU_LLM_ON_MIN_CORES and m.ram_gb >= CPU_LLM_ON_MIN_RAM_GB


def plan(m: Machine) -> Plan:
    """机器画像 → 一套配置。纯函数。"""
    # ── Apple 芯片:MLX,统一内存 ──
    if m.apple_silicon:
        ram = f"{m.ram_gb:.0f} GB"
        if m.ram_gb >= APPLE_LARGE_RAM_GB:
            return Plan(
                "apple-large",
                STT_MLX_LARGE,
                LLM_MLX_LARGE,
                True,
                True,
                bi(f"Apple 芯片,内存 {ram}", f"Apple Silicon, {ram} of memory"),
            )
        return Plan(
            "apple-small",
            STT_MLX_SMALL,
            LLM_MLX_SMALL,
            True,
            True,
            bi(
                f"Apple 芯片,内存 {ram}(不到 16 GB,用小一号的模型)",
                f"Apple Silicon, {ram} of memory (under 16 GB, so the smaller models)",
            ),
        )

    # ── 独立显卡(llama.cpp 认得出来的)──
    if m.gpu and m.llama_cpp and m.vram_gb >= GPU_MIN_VRAM_GB:
        card = (m.gpu_name or m.gpu.upper()).strip()
        vram = f"{m.vram_gb:.0f} GB"
        if m.vram_gb >= GPU_LARGE_VRAM_GB:
            return Plan(
                "gpu-large",
                STT_QWEN_LARGE,
                LLM_GGUF_LARGE,
                True,
                True,
                bi(
                    f"{card},显存 {vram}:两个大模型都放得下",
                    f"{card}, {vram} of VRAM: room for both large models",
                ),
            )
        if m.vram_gb >= GPU_BOTH_VRAM_GB:
            return Plan(
                "gpu-both",
                STT_QWEN_SMALL,
                LLM_GGUF_SMALL,
                True,
                True,
                bi(
                    f"{card},显存 {vram}:识别和后处理都放在显卡上",
                    f"{card}, {vram} of VRAM: recognition and post-processing both on the GPU",
                ),
            )
        return Plan(
            "gpu-stt-only",
            STT_QWEN_SMALL,
            LLM_GGUF_SMALL,
            False,
            _cpu_llm_on(m),
            bi(
                f"{card},显存 {vram}:只够放识别模型,后处理跑在 CPU 上(慢)",
                f"{card}, {vram} of VRAM: only enough for recognition; "
                f"post-processing runs on the CPU (slow)",
            ),
        )

    # ── 没有可用的显卡 ──
    stt = _cpu_stt(m)
    spec = f"{m.cores} 线程 / {m.ram_gb:.0f} GB"
    spec_en = f"{m.cores} threads / {m.ram_gb:.0f} GB"
    missing = "" if m.llama_cpp else ";环境里没有 llama.cpp"
    missing_en = "" if m.llama_cpp else "; llama.cpp is not installed"
    return Plan(
        "cpu-qwen" if stt == STT_QWEN_SMALL else "cpu-whisper",
        stt,
        LLM_GGUF_SMALL,
        False,
        _cpu_llm_on(m),
        bi(
            f"没有可用的独立显卡,CPU {spec}{missing}",
            f"no usable discrete GPU, CPU {spec_en}{missing_en}",
        ),
    )
