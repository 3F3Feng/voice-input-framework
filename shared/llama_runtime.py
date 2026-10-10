"""让 CUDA 版的 llama.cpp 找得到 CUDA 运行库。

`llama-cpp-python` 的 CUDA 预编译包**不带** CUDA 运行库:它的 `ggml-cuda` 要
`cudart` 和 `cublas`(CUDA 工具包里的),外加显卡驱动里的 `nvcuda` / `libcuda`。
普通用户不会去装 CUDA 工具包。不过这两个库其实已经在环境里了——PyTorch 的 CUDA 版
带着同一个大版本(12.x)的:

- Windows:就在 `site-packages/torch/lib/` 里(`cudart64_12.dll`、`cublas64_12.dll`、
  `cublasLt64_12.dll`);
- Linux:PyTorch 依赖的 `nvidia-*-cu12` 包,在 `site-packages/nvidia/<包>/lib/` 里。

系统默认不会去这些目录里找,所以在 `import llama_cpp` **之前**调一次
:func:`prepare`:Windows 上把目录加进 DLL 搜索路径,Linux 上把库先加载进来(之后
llama.cpp 按名字找时用的就是已经加载的那份)。找不到也不报错——那时要么装的不是 CUDA
版(本来就不需要),要么 `import llama_cpp` 自己会失败并说出缺了哪个库,建环境脚本据此
退回 Vulkan / CPU 版。

只查目录、按路径加载库,不 `import torch`(那要好几秒,还会占显存)。

Windows 上还有一样东西预编译包不带:Visual C++ 运行库(`msvcp140.dll`)。进程加载到的
那一份太旧时,llama.cpp 装得上、也 import 得了,一调用就崩——见 :func:`msvc_runtime`。
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

logger = logging.getLogger(__name__)

#: Linux 上要预先加载的库,按依赖顺序(cublas 依赖 cublasLt)。
LINUX_LIBRARIES = ("libcudart.so.12", "libcublasLt.so.12", "libcublas.so.12")

#: `msvcp140.dll` 至少要这个版本。llama-cpp-python 的 Windows 包是用 MSVC 14.40 之后的
#: 工具链编的(0.3.36 是 14.44),那以后 `std::mutex` 的构造不再初始化内部指针,靠新版
#: 运行库在加锁时认;旧运行库照着空指针去读,第一次加锁就是
#: `OSError: exception: access violation reading 0x0000000000000000`。
#: CUDA / Vulkan / CPU 三种包都一样,换一种装也没用。
#:
#: 「旧运行库」不一定是系统里那份:Windows 找 DLL 时 python.exe 所在的目录排在 System32
#: 前面,而 Anaconda 的 Python 目录里自带一份(实测遇到的是 14.27,系统里明明是 14.50)。
#: 所以建环境脚本在 Windows 上用 uv 自己管理的 Python——它的目录里没有这个文件。
MIN_MSVC_RUNTIME = (14, 40)

_prepared: list[str] | None = None


@dataclass(frozen=True)
class MsvcRuntime:
    """这个进程用的 `msvcp140.dll`:是哪一份、什么版本。"""

    path: str
    version: tuple[int, ...]

    @property
    def version_text(self) -> str:
        return ".".join(str(n) for n in self.version)

    @property
    def too_old(self) -> bool:
        return self.version[:2] < MIN_MSVC_RUNTIME

    @property
    def from_system(self) -> bool:
        """是不是 System32 里那份(否则就是 Python 安装自带、抢在系统前面的)。"""
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        return PureWindowsPath(self.path).parent == PureWindowsPath(root) / "System32"


def _package_dir(name: str) -> Path | None:
    """包所在的目录,不 import 它。没装时返回 None。"""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    return Path(spec.origin).parent if spec.origin else None


def library_dirs(
    platform: str | None = None,
    torch_dir: Path | None = None,
    nvidia_dir: Path | None = None,
) -> list[Path]:
    """环境里可能放着 CUDA 运行库的目录(只返回真实存在的)。

    `torch_dir` / `nvidia_dir` 默认现查;测试里直接传。
    """
    platform = platform or sys.platform
    torch_dir = torch_dir if torch_dir is not None else _package_dir("torch")
    nvidia_dir = nvidia_dir if nvidia_dir is not None else _package_dir("nvidia")
    sub = "bin" if platform == "win32" else "lib"
    dirs: list[Path] = []
    if torch_dir is not None:
        dirs.append(torch_dir / "lib")
    if nvidia_dir is not None:
        try:
            dirs.extend(sorted(p / sub for p in nvidia_dir.iterdir() if p.is_dir()))
        except OSError:
            pass
    return [d for d in dirs if d.is_dir()]


def prepare() -> list[str]:
    """把 CUDA 运行库备好(见模块说明)。重复调用只做一次,返回用上的目录 / 库。"""
    global _prepared
    if _prepared is not None:
        return _prepared
    used: list[str] = []
    dirs = library_dirs()
    if sys.platform == "win32":
        for d in dirs:
            if not any(d.glob("cudart64_*.dll")) and not any(d.glob("cublas64_*.dll")):
                continue
            # llama-cpp-python 加载自己的 DLL 时走的是传统搜索顺序(看 PATH),不看
            # add_dll_directory 加的目录;两样都加上,哪种加载方式都找得到。
            os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                os.add_dll_directory(str(d))
            except (OSError, AttributeError):
                pass
            used.append(str(d))
    elif sys.platform.startswith("linux"):
        import ctypes

        for name in LINUX_LIBRARIES:
            for d in dirs:
                path = d / name
                if path.is_file():
                    try:
                        ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
                        used.append(str(path))
                    except OSError as e:
                        logger.debug(f"could not preload {path}: {e}")
                    break
    if used:
        logger.info(f"CUDA runtime for llama.cpp: {', '.join(used)}")
    _prepared = used
    return used


def _file_version(path: str) -> tuple[int, ...] | None:
    """Windows 文件属性里的版本号(四段)。读不到返回 None。"""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("version", use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    api.GetFileVersionInfoW.restype = wintypes.BOOL
    api.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    api.VerQueryValueW.restype = wintypes.BOOL

    size = api.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(path, 0, size, data):
        return None
    block = ctypes.c_void_p()
    length = wintypes.UINT()
    if not api.VerQueryValueW(data, "\\", ctypes.byref(block), ctypes.byref(length)):
        return None
    # VS_FIXEDFILEINFO:13 个 DWORD,第 3、4 个是文件版本的高低两半
    if not block.value or length.value < 16:
        return None
    info = ctypes.cast(block, ctypes.POINTER(wintypes.DWORD * 4)).contents
    return (info[2] >> 16, info[2] & 0xFFFF, info[3] >> 16, info[3] & 0xFFFF)


def msvc_runtime() -> MsvcRuntime | None:
    """这个进程用的是哪一份 `msvcp140.dll`。不是 Windows、或者查不出来时返回 None。

    在 `import llama_cpp` **之后**调最准:那时库已经加载,问到的就是 llama.cpp 实际用的
    那一份(System32 里的,或者 Python 安装自带的)。还没加载时按名字加载一次再问。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        kernel32.GetModuleFileNameW.restype = wintypes.DWORD

        handle = kernel32.GetModuleHandleW("msvcp140.dll")
        if not handle:
            handle = ctypes.WinDLL("msvcp140.dll")._handle
        buffer = ctypes.create_unicode_buffer(32768)
        if not kernel32.GetModuleFileNameW(handle, buffer, len(buffer)):
            return None
        version = _file_version(buffer.value)
        return MsvcRuntime(buffer.value, version) if version else None
    except Exception as e:  # 查不出来不算错:调用方当作「没发现问题」
        logger.debug(f"could not inspect msvcp140.dll: {e}")
        return None
