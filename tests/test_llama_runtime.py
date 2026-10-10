"""CUDA 版 llama.cpp 的运行库从哪儿来(shared/llama_runtime.py)。

预编译的 CUDA 包不带 cudart / cublas;PyTorch 的 CUDA 版带着。这里钉住「去哪些目录找」
——真正的加载只有在有 NVIDIA 显卡的机器上才验证得了。
"""

import sys
from pathlib import Path

import pytest

from shared import llama_runtime


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(llama_runtime, "_prepared", None)


def make_tree(tmp_path, sub):
    torch = tmp_path / "torch"
    (torch / "lib").mkdir(parents=True)
    nvidia = tmp_path / "nvidia"
    for pkg in ("cublas", "cuda_runtime"):
        (nvidia / pkg / sub).mkdir(parents=True)
    (nvidia / "cudnn").mkdir()  # 没有库目录的包:跳过
    (nvidia / "__init__.py").write_text("")  # 文件不是包目录
    return torch, nvidia


def test_windows_looks_in_torch_lib_and_nvidia_bin(tmp_path):
    torch, nvidia = make_tree(tmp_path, "bin")
    dirs = llama_runtime.library_dirs("win32", torch, nvidia)
    assert dirs == [torch / "lib", nvidia / "cublas" / "bin", nvidia / "cuda_runtime" / "bin"]


def test_linux_looks_in_nvidia_lib(tmp_path):
    torch, nvidia = make_tree(tmp_path, "lib")
    dirs = llama_runtime.library_dirs("linux", torch, nvidia)
    assert dirs == [torch / "lib", nvidia / "cublas" / "lib", nvidia / "cuda_runtime" / "lib"]


def test_missing_packages_are_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(llama_runtime, "_package_dir", lambda name: None)
    assert llama_runtime.library_dirs("win32") == []
    # 目录不存在(包装了一半)也不报错
    assert llama_runtime.library_dirs("linux", tmp_path / "nope", tmp_path / "nope2") == []
    assert llama_runtime.prepare() == []


def test_package_dir_does_not_import_the_package():
    assert llama_runtime._package_dir("definitely_not_installed_xyz") is None
    before = set(sys.modules)
    found = llama_runtime._package_dir("json")
    assert found is not None and found.name == "json"
    assert "torch" not in set(sys.modules) - before


def test_windows_prepare_puts_the_cuda_dirs_on_path(tmp_path, monkeypatch):
    torch, nvidia = make_tree(tmp_path, "bin")
    (torch / "lib" / "cudart64_12.dll").write_bytes(b"")
    (torch / "lib" / "cublas64_12.dll").write_bytes(b"")
    added = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        llama_runtime, "library_dirs", lambda: [torch / "lib", nvidia / "cublas" / "bin"]
    )
    monkeypatch.setattr(llama_runtime.os, "add_dll_directory", added.append, raising=False)
    monkeypatch.setenv("PATH", "C:\\Windows")
    used = llama_runtime.prepare()
    # 只加真的放着 CUDA 运行库的目录(nvidia/cublas/bin 是空的)
    assert used == [str(torch / "lib")]
    assert llama_runtime.os.environ["PATH"].startswith(str(torch / "lib"))
    assert llama_runtime.os.environ["PATH"].endswith("C:\\Windows")
    assert added == [str(torch / "lib")]
    # 重复调用不重复加
    assert llama_runtime.prepare() == used
    assert llama_runtime.os.environ["PATH"].count(str(torch / "lib")) == 1


def test_linux_prepare_preloads_in_dependency_order(tmp_path, monkeypatch):
    import ctypes

    torch, nvidia = make_tree(tmp_path, "lib")
    (nvidia / "cublas" / "lib" / "libcublas.so.12").write_bytes(b"")
    (nvidia / "cublas" / "lib" / "libcublasLt.so.12").write_bytes(b"")
    (nvidia / "cuda_runtime" / "lib" / "libcudart.so.12").write_bytes(b"")
    loaded = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        llama_runtime,
        "library_dirs",
        lambda: [nvidia / "cublas" / "lib", nvidia / "cuda_runtime" / "lib"],
    )
    monkeypatch.setattr(ctypes, "CDLL", lambda path, mode=0: loaded.append(path))
    used = llama_runtime.prepare()
    assert [Path(p).name for p in loaded] == list(llama_runtime.LINUX_LIBRARIES)
    assert used == loaded


def test_a_library_that_will_not_load_is_skipped_not_fatal(tmp_path, monkeypatch):
    import ctypes

    lib = tmp_path / "lib"
    lib.mkdir()
    for name in llama_runtime.LINUX_LIBRARIES:
        (lib / name).write_bytes(b"not a real library")

    def refuse(path, mode=0):
        raise OSError("invalid ELF header")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(llama_runtime, "library_dirs", lambda: [lib])
    monkeypatch.setattr(ctypes, "CDLL", refuse)
    assert llama_runtime.prepare() == []


# ── Windows 的 Visual C++ 运行库 ──────────────────────────────────────────
#
# 实际遇到的:一台 RTX 4090 的机器上 CUDA / Vulkan / CPU 三种 llama.cpp 都在第一次调用时
# 报 `access violation reading 0x0000000000000000`。预编译包是 MSVC 14.44 编的,要系统里的
# msvcp140.dll 不早于 14.40。


def test_a_runtime_older_than_the_toolchain_is_too_old():
    system32 = r"C:\Windows\System32\msvcp140.dll"
    assert llama_runtime.MsvcRuntime(system32, (14, 36, 32532, 0)).too_old
    assert llama_runtime.MsvcRuntime(system32, (14, 29, 30133, 0)).too_old
    assert not llama_runtime.MsvcRuntime(system32, (14, 40, 33810, 0)).too_old
    assert not llama_runtime.MsvcRuntime(system32, (14, 44, 35211, 0)).too_old
    assert not llama_runtime.MsvcRuntime(system32, (15, 0, 0, 0)).too_old
    assert llama_runtime.MsvcRuntime(system32, (14, 36, 32532, 0)).version_text == "14.36.32532.0"


@pytest.mark.skipif(sys.platform == "win32", reason="other platforms have no msvcp140.dll")
def test_there_is_no_msvc_runtime_to_check_off_windows():
    assert llama_runtime.msvc_runtime() is None


@pytest.mark.skipif(sys.platform != "win32", reason="reads the real msvcp140.dll")
def test_the_real_runtime_is_found_and_its_version_read():
    runtime = llama_runtime.msvc_runtime()
    assert runtime is not None, "no msvcp140.dll could be loaded on this machine"
    assert Path(runtime.path).name.lower() == "msvcp140.dll"
    assert len(runtime.version) == 4 and runtime.version[0] == 14, runtime


def test_a_runtime_next_to_python_is_not_the_system_one(monkeypatch):
    # 实测:Anaconda 的 Python 目录里自带 14.27,排在 System32 的 14.50 前面
    monkeypatch.setenv("SystemRoot", r"C:\WINDOWS")
    bundled = llama_runtime.MsvcRuntime(r"E:\anaconda3\MSVCP140.dll", (14, 27, 29016, 0))
    assert bundled.too_old and not bundled.from_system
    system = llama_runtime.MsvcRuntime(r"C:\Windows\SYSTEM32\MSVCP140.dll", (14, 50, 35719, 0))
    assert system.from_system and not system.too_old
