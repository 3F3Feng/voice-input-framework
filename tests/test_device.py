#!/usr/bin/env python3
"""后端选择的单测。

AMD / Intel / NVIDIA 的硬件手边都没有,所以这里注入假的 torch 对象来覆盖
那几条分支 —— 判断逻辑本身是纯的,值得钉住,尤其是 ROCm 那条:它必须排在
CUDA 前面,否则 A 卡会被一路报成 N 卡。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.device import Backend, detect  # noqa: E402


def fake_torch(*, mps=False, cuda=False, hip=None, cuda_ver=None, xpu=False, capability=(7, 5)):
    """凑一个够用的假 torch。"""
    return SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
        version=SimpleNamespace(hip=hip, cuda=cuda_ver),
        cuda=SimpleNamespace(
            is_available=lambda: cuda,
            get_device_capability=lambda _i: capability,
            get_device_name=lambda _i: "FakeGPU",
        ),
        xpu=SimpleNamespace(is_available=lambda: xpu) if xpu else None,
        cpu=SimpleNamespace(),
        get_num_threads=lambda: 8,
    )


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv("VIF_DEVICE", raising=False)
    monkeypatch.delenv("VIF_DTYPE", raising=False)


class TestBackendDetection:
    def test_apple_silicon_uses_fp16_not_fp32(self):
        """MPS 支持半精度。以前这里硬编码 fp32,白白浪费一半带宽。"""
        b = detect(fake_torch(mps=True))
        assert b.name == "mps"
        assert b.torch_device == "mps"
        assert b.dtype_name == "float16"

    def test_rocm_is_not_reported_as_cuda(self):
        """回归:ROCm 版 torch 的 cuda.is_available() 也是 True。

        必须先看 torch.version.hip,否则 AMD 卡会被一路报成 NVIDIA,
        日志里看不出真实硬件。注意设备名仍然是 "cuda" —— ROCm 就是这么用的。
        """
        b = detect(fake_torch(cuda=True, hip="6.2.0"))
        assert b.name == "rocm"
        assert b.torch_device == "cuda"
        assert "AMD" in b.detail and "6.2.0" in b.detail

    def test_nvidia_ampere_and_newer_prefers_bf16(self):
        """SM80+ 原生支持 bf16,数值范围比 fp16 稳,不容易出 NaN。"""
        b = detect(fake_torch(cuda=True, cuda_ver="12.4", capability=(8, 6)))
        assert (b.name, b.dtype_name) == ("cuda", "bfloat16")

    def test_nvidia_pre_ampere_stays_on_fp16(self):
        b = detect(fake_torch(cuda=True, cuda_ver="12.4", capability=(7, 5)))
        assert (b.name, b.dtype_name) == ("cuda", "float16")

    def test_intel_xpu_is_detected(self):
        b = detect(fake_torch(xpu=True))
        assert (b.name, b.torch_device) == ("xpu", "xpu")

    def test_cpu_without_bf16_hardware_stays_fp32(self):
        """没有 AVX512-BF16 / AMX 时 bf16 是软件模拟,比 fp32 还慢。"""
        b = detect(fake_torch())
        assert (b.name, b.dtype_name) == ("cpu", "float32")

    def test_cpu_with_bf16_hardware_uses_bf16(self):
        t = fake_torch()
        t.cpu._is_avx512_bf16_supported = lambda: True
        assert detect(t).dtype_name == "bfloat16"

    def test_probe_that_raises_does_not_break_detection(self):
        """部分 torch 构建里这些探测函数会抛,不能让它带崩整个检测。"""
        t = fake_torch()

        def boom():
            raise RuntimeError("not supported in this build")

        t.cpu._is_avx512_bf16_supported = boom
        assert detect(t).dtype_name == "float32"

    def test_priority_apple_beats_everything(self):
        b = detect(fake_torch(mps=True, cuda=True, cuda_ver="12.4", xpu=True))
        assert b.name == "mps"


class TestEnvOverrides:
    def test_device_can_be_forced(self, monkeypatch):
        monkeypatch.setenv("VIF_DEVICE", "cpu")
        b = detect(fake_torch(mps=True))
        assert (b.name, b.torch_device) == ("cpu", "cpu")

    def test_dtype_can_be_forced(self, monkeypatch):
        monkeypatch.setenv("VIF_DTYPE", "float32")
        b = detect(fake_torch(mps=True))
        assert (b.name, b.dtype_name) == ("mps", "float32")

    def test_nonsense_dtype_is_ignored(self, monkeypatch):
        monkeypatch.setenv("VIF_DTYPE", "float8_banana")
        assert detect(fake_torch(mps=True)).dtype_name == "float16"


def test_as_dict_shape_is_stable():
    """/health 和界面按这个形状读,字段名别乱改。"""
    d = Backend("cpu", "cpu", "float32", "CPU（8 线程）").as_dict()
    assert set(d) == {"backend", "device", "dtype", "detail"}


class TestMachineProfileAndRecommendation:
    """机器画像 → 模型推荐。

    门槛来自一台 Intel i5-9400(6 核 / 8GB / 无独显)上的实测:
        whisper_tiny  0.37x 实时
        whisper_base  0.70x 实时
        whisper_small 2.31x 实时  ← 说 10 秒要等 23 秒,不能当默认
    所以 6 核这一档必须落在 base,这条是钉死的回归。
    """

    @staticmethod
    def prof(backend_name, cores, ram, vram=None):
        from services.device import Backend, MachineProfile

        dev = {"mps": "mps", "cuda": "cuda", "rocm": "cuda", "xpu": "xpu"}.get(backend_name, "cpu")
        return MachineProfile(Backend(backend_name, dev, "float16", "x"), cores, ram, vram)

    def test_six_core_cpu_gets_base_not_small(self):
        from services.device import recommend_stt_model

        model, why = recommend_stt_model(self.prof("cpu", 6, 7.6))
        assert model == "whisper_base", f"6 核应当选 base(实测 small 是 2.31x 实时),却选了 {model}"
        assert "6" in why

    def test_many_core_cpu_can_afford_small(self):
        from services.device import recommend_stt_model

        assert recommend_stt_model(self.prof("cpu", 32, 64.0))[0] == "whisper_small"

    def test_tiny_cpu_gets_tiny(self):
        from services.device import recommend_stt_model

        assert recommend_stt_model(self.prof("cpu", 2, 4.0))[0] == "whisper_tiny"

    def test_memory_overrides_speed_when_tighter(self):
        """核多但内存小:不能因为算得动就挑一个装不下的。"""
        from services.device import recommend_stt_model

        model, why = recommend_stt_model(self.prof("cpu", 32, 3.0))
        assert model == "whisper_tiny"
        assert "内存不够" in why

    def test_unknown_ram_falls_back_to_smallest(self):
        """内存量不出来(返回 0)时按最保守的来,不要赌。"""
        from services.device import recommend_stt_model

        assert recommend_stt_model(self.prof("cpu", 32, 0.0))[0] == "whisper_tiny"

    def test_big_gpu_gets_the_large_model(self):
        from services.device import recommend_stt_model

        model, why = recommend_stt_model(self.prof("cuda", 8, 32.0, vram=24.0))
        assert model == "whisper_turbo"
        assert "24" in why

    def test_small_gpu_does_not_overcommit_vram(self):
        """4GB 卡按 70% 余量算只有 2.8GB,放不下 medium(4GB 档)。"""
        from services.device import recommend_stt_model

        assert recommend_stt_model(self.prof("cuda", 8, 16.0, vram=4.0))[0] == "whisper_small"

    def test_rocm_is_treated_as_a_gpu(self):
        from services.device import recommend_stt_model

        model, why = recommend_stt_model(self.prof("rocm", 16, 32.0, vram=16.0))
        assert model == "whisper_turbo"
        assert "ROCM" in why.upper()

    def test_apple_silicon_picks_mlx_by_unified_memory(self):
        from services.device import recommend_stt_model

        assert recommend_stt_model(self.prof("mps", 14, 36.0))[0] == "qwen_asr_mlx_native"
        assert recommend_stt_model(self.prof("mps", 8, 8.0))[0] == "qwen_asr_mlx_native_small"

    def test_every_recommended_model_actually_exists(self):
        """推荐出来的名字必须在模型注册表里,否则启动就炸。"""
        from shared.model_registry import MODELS_CONFIG
        from services.device import recommend_stt_model

        cases = [
            ("cpu", 2, 4.0, None),
            ("cpu", 6, 8.0, None),
            ("cpu", 32, 64.0, None),
            ("cpu", 32, 3.0, None),
            ("cuda", 8, 32.0, 24.0),
            ("cuda", 8, 16.0, 4.0),
            ("cuda", 8, 16.0, 1.5),
            ("rocm", 16, 32.0, 16.0),
            ("xpu", 8, 16.0, 8.0),
            ("mps", 14, 36.0, None),
            ("mps", 8, 8.0, None),
        ]
        for name, cores, ram, vram in cases:
            model, _ = recommend_stt_model(self.prof(name, cores, ram, vram))
            assert model in MODELS_CONFIG, f"{name}/{cores}核/{ram}GB 推荐了不存在的模型 {model}"
