"""机器画像 → 一套配置(shared/hardware_plan.py),和探测里的纯逻辑(services/hardware.py)。

三个系统、四类硬件,每类按显存 / 内存分几档。表里的门槛是「两个模型都放得下、还给桌面
留一截」算出来的;这里把每一档钉住,免得以后改一个数字时别的档位跟着变了没人知道。
"""

import pytest

from services import hardware
from shared import hardware_plan as hp
from shared.hardware_plan import Machine, plan
from shared.model_registry import MODELS_CONFIG


def gpu(kind: str, vram: float, ram: float = 32, cores: int = 12, name: str = "") -> Machine:
    return Machine(gpu=kind, gpu_name=name, vram_gb=vram, ram_gb=ram, cores=cores)


@pytest.mark.parametrize(
    ("machine", "tier", "stt", "llm", "llm_on_gpu", "llm_on"),
    [
        # ── macOS · Apple 芯片(MLX)──
        (
            Machine(apple_silicon=True, ram_gb=36, cores=14),
            "apple-large",
            "qwen_asr_mlx_native",
            "Gemma-4-E4B",
            True,
            True,
        ),
        (
            Machine(apple_silicon=True, ram_gb=15.9, cores=10),
            "apple-large",
            "qwen_asr_mlx_native",
            "Gemma-4-E4B",
            True,
            True,
        ),
        (
            Machine(apple_silicon=True, ram_gb=8, cores=8),
            "apple-small",
            "qwen_asr_mlx_native_small",
            "Gemma-4-E2B",
            True,
            True,
        ),
        # ── Windows / Linux · NVIDIA(CUDA)──
        (gpu("cuda", 24, name="RTX 4090"), "gpu-large", "qwen_asr", "Gemma-4-E4B-GGUF", True, True),
        (gpu("cuda", 12, name="RTX 3060"), "gpu-large", "qwen_asr", "Gemma-4-E4B-GGUF", True, True),
        (
            gpu("cuda", 8, ram=16, cores=8, name="GTX 1070 Ti"),
            "gpu-both",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            True,
            True,
        ),
        (
            gpu("cuda", 6, name="RTX 2060"),
            "gpu-both",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            True,
            True,
        ),
        (
            gpu("cuda", 4, ram=16, cores=12, name="GTX 1650"),
            "gpu-stt-only",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            False,
            True,
        ),
        (
            gpu("cuda", 4, ram=8, cores=4, name="GTX 1650"),
            "gpu-stt-only",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
        # ── Windows / Linux · AMD、Intel Arc(Vulkan)──
        (
            gpu("vulkan", 16, name="RX 7800 XT"),
            "gpu-large",
            "qwen_asr",
            "Gemma-4-E4B-GGUF",
            True,
            True,
        ),
        (
            gpu("vulkan", 8, name="RX 6600"),
            "gpu-both",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            True,
            True,
        ),
        (
            gpu("vulkan", 8, name="Intel Arc A750"),
            "gpu-both",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            True,
            True,
        ),
        (
            gpu("vulkan", 4, ram=8, cores=6, name="RX 580 4GB"),
            "gpu-stt-only",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
        # 显存太小的显卡当成没有
        (
            gpu("vulkan", 2, ram=16, cores=8, name="GT 1030"),
            "cpu-qwen",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
        # ── 任何系统 · 没有独立显卡 ──
        (
            Machine(ram_gb=32, cores=16),
            "cpu-qwen",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            False,
            True,
        ),
        (
            Machine(ram_gb=16, cores=12),
            "cpu-qwen",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            False,
            True,
        ),
        (
            Machine(ram_gb=16, cores=8),
            "cpu-qwen",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
        (
            Machine(ram_gb=8, cores=6),
            "cpu-qwen",
            "qwen_asr_small",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
        (
            Machine(ram_gb=8, cores=4),
            "cpu-whisper",
            "whisper_base",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
        (
            Machine(ram_gb=4, cores=4),
            "cpu-whisper",
            "whisper_base",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
        (
            Machine(ram_gb=3, cores=2),
            "cpu-whisper",
            "whisper_tiny",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
        # 量不出内存(0):按最保守的来
        (
            Machine(ram_gb=0, cores=8),
            "cpu-whisper",
            "whisper_tiny",
            "Gemma-4-E2B-GGUF",
            False,
            False,
        ),
    ],
)
def test_every_tier(machine, tier, stt, llm, llm_on_gpu, llm_on):
    p = plan(machine)
    assert (p.tier, p.stt_model, p.llm_model, p.llm_on_gpu, p.llm_default_on) == (
        tier,
        stt,
        llm,
        llm_on_gpu,
        llm_on,
    )
    assert p.why  # 每一档都说得出为什么


def test_a_gpu_that_llama_cpp_cannot_use_does_not_count():
    """显卡在,但环境里没有 llama.cpp(老环境):量化版 Qwen3-ASR 用不了,不能选它"""
    old_env = Machine(gpu="cuda", vram_gb=24, ram_gb=64, cores=32, llama_cpp=False)
    p = plan(old_env)
    assert p.tier == "cpu-whisper"
    assert p.stt_model == "whisper_base"
    assert "llama.cpp" in p.why


def test_planned_models_fit_in_the_vram_with_room_for_the_desktop():
    """表里的门槛就是这么来的:两个模型加起来,至少给桌面留 0.4 GB"""
    need = {
        "qwen_asr_small": 1.3,
        "qwen_asr": 3.0,
        "Gemma-4-E2B-GGUF": 3.8,
        "Gemma-4-E4B-GGUF": 6.0,
    }
    for vram in (
        hp.GPU_MIN_VRAM_GB,
        4,
        hp.GPU_BOTH_VRAM_GB,
        6,
        8,
        10.9,
        hp.GPU_LARGE_VRAM_GB,
        12,
        16,
        24,
    ):
        p = plan(gpu("cuda", vram))
        used = need[p.stt_model] + (need[p.llm_model] if p.llm_on_gpu else 0)
        assert used + 0.4 <= vram, (vram, p.tier, used)


def test_every_planned_model_exists():
    """表里写的名字得真有这个模型"""
    from services.llm_server import LlamaCppBackend, MLXBackend

    for name in (
        hp.STT_MLX_LARGE,
        hp.STT_MLX_SMALL,
        hp.STT_QWEN_LARGE,
        hp.STT_QWEN_SMALL,
        hp.STT_WHISPER_BASE,
        hp.STT_WHISPER_TINY,
    ):
        assert name in MODELS_CONFIG, name
    assert {hp.LLM_MLX_LARGE, hp.LLM_MLX_SMALL} <= set(MLXBackend.MODEL_IDS)
    assert {hp.LLM_GGUF_LARGE, hp.LLM_GGUF_SMALL} <= set(LlamaCppBackend.MODEL_IDS)
    for name in (hp.STT_QWEN_LARGE, hp.STT_QWEN_SMALL):
        info = MODELS_CONFIG[name]
        assert info["engine"] == "qwen_asr_gguf"
        assert info["gguf_file"].endswith(".gguf") and info["mmproj_file"].startswith("mmproj-")
        assert not info.get("requires_apple_silicon")


def test_plan_serializes_in_both_languages():
    p = plan(gpu("cuda", 8, name="GTX 1070 Ti"))
    zh, en = p.as_dict("zh"), p.as_dict("en")
    assert zh["tier"] == en["tier"] == "gpu-both"
    assert "显存" in zh["why"] and "VRAM" in en["why"]
    assert "GTX 1070 Ti" in zh["why"] and "GTX 1070 Ti" in en["why"]


# ── 探测(services/hardware.py)里的纯逻辑 ──


def test_gpu_backend_comes_from_the_llama_cpp_device_name():
    assert hardware.gpu_kind("CUDA0") == "cuda"
    assert hardware.gpu_kind("Vulkan1") == "vulkan"
    assert hardware.gpu_kind("MTL0") == "metal"
    assert hardware.gpu_kind("SYCL0") == "sycl"


def test_only_discrete_gpus_count_and_the_biggest_wins():
    devices = [
        {"name": "CPU", "type": hardware.DEVICE_CPU, "total_bytes": 64 * hardware.GB},
        {"name": "Vulkan0", "type": hardware.DEVICE_IGPU, "total_bytes": 32 * hardware.GB},
        {"name": "Vulkan1", "type": hardware.DEVICE_GPU, "total_bytes": 8 * hardware.GB},
        {"name": "CUDA0", "type": hardware.DEVICE_GPU, "total_bytes": 12 * hardware.GB},
        {"name": "BLAS", "type": hardware.DEVICE_ACCEL, "total_bytes": 0},
    ]
    assert hardware.pick_gpu(devices)["name"] == "CUDA0"
    # 只有集成显卡:当成没有显卡(它用的是系统内存,跑 Vulkan 也不比 CPU 快)
    assert hardware.pick_gpu(devices[:2]) is None
    assert hardware.pick_gpu([]) is None


def test_machine_follows_what_llama_cpp_sees(monkeypatch):
    hardware.machine.cache_clear()
    monkeypatch.setattr(hardware, "IS_APPLE_SILICON", False)
    monkeypatch.setattr(hardware.llm_backend, "has_package", lambda name: True)
    monkeypatch.setattr(hardware.os, "cpu_count", lambda: 8)
    monkeypatch.setattr("services.device.total_ram_gb", lambda: 16.0)
    monkeypatch.setattr(
        hardware,
        "llama_devices",
        lambda: [
            {
                "name": "CUDA0",
                "description": "NVIDIA GeForce GTX 1070 Ti",
                "type": hardware.DEVICE_GPU,
                "total_bytes": 8 * hardware.GB,
                "free_bytes": 7 * hardware.GB,
            },
        ],
    )
    m = hardware.machine()
    assert (m.gpu, m.gpu_name, round(m.vram_gb), m.ram_gb, m.cores) == (
        "cuda",
        "NVIDIA GeForce GTX 1070 Ti",
        8,
        16.0,
        8,
    )
    assert plan(m).tier == "gpu-both"

    # 装的是 CPU 版的 llama.cpp(设备表里没有显卡):按没有显卡来选
    hardware.machine.cache_clear()
    monkeypatch.setattr(hardware, "llama_devices", lambda: [])
    assert hardware.machine().gpu is None
    # 环境里压根没有 llama.cpp
    hardware.machine.cache_clear()
    monkeypatch.setattr(hardware.llm_backend, "has_package", lambda name: False)
    m = hardware.machine()
    assert (m.gpu, m.llama_cpp) == (None, False)
    hardware.machine.cache_clear()


def test_recommended_stt_falls_back_when_the_planned_model_is_unavailable(monkeypatch):
    """老环境没装 llama.cpp:配置表选的量化版用不了,退回按 PyTorch 看硬件挑 Whisper"""
    from services import model_catalog

    monkeypatch.setattr(
        hardware,
        "current_plan",
        lambda: plan(gpu("cuda", 8, name="GTX 1070 Ti")),
    )
    monkeypatch.setattr(model_catalog, "unavailable_reason", lambda info: None)
    assert hardware.recommended_stt_model()[0] == "qwen_asr_small"

    monkeypatch.setattr(model_catalog, "unavailable_reason", lambda info: "没有 llama.cpp")
    monkeypatch.setattr("services.device.profile", lambda: "profile")
    monkeypatch.setattr(
        "services.device.recommend_stt_model", lambda p: ("whisper_turbo", "显存 8GB")
    )
    assert hardware.recommended_stt_model() == ("whisper_turbo", "显存 8GB")


def test_only_the_small_gpu_tier_keeps_the_llm_off_the_gpu():
    """「没探测到显卡」不等于「不许用显卡」:探测可能失败,llama.cpp 自己认得出就照用"""
    assert plan(gpu("cuda", 4)).reserve_gpu_for_stt is True
    for m in (
        gpu("cuda", 8),
        gpu("vulkan", 24),
        Machine(ram_gb=32, cores=16),
        Machine(ram_gb=4, cores=2),
        Machine(apple_silicon=True, ram_gb=16, cores=8),
    ):
        assert plan(m).reserve_gpu_for_stt is False, m


def test_ggml_functions_are_found_in_whichever_library_exports_them():
    """Windows 上按名字找函数只看点名的那个 DLL:设备注册表在 ggml,设备本身在 ggml-base"""

    class Lib:
        def __init__(self, **functions):
            for name, fn in functions.items():
                setattr(self, name, fn)

    def make(result):
        def fn(*args):
            return result

        return fn

    ggml = object.__new__(hardware._Ggml)
    ggml._libs = [Lib(ggml_backend_dev_count=make(2)), Lib(ggml_backend_dev_name=make(b"CUDA0"))]
    assert ggml.function("ggml_backend_dev_count", None, [])() == 2
    assert ggml.function("ggml_backend_dev_name", None, [])() == b"CUDA0"
    with pytest.raises(AttributeError):
        ggml.function("ggml_backend_dev_nope", None, [])
