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
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Linux 上要预先加载的库,按依赖顺序(cublas 依赖 cublasLt)。
LINUX_LIBRARIES = ("libcudart.so.12", "libcublasLt.so.12", "libcublas.so.12")

_prepared: list[str] | None = None


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
