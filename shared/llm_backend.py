"""LLM 后处理用哪个推理后端,以及这台机器上能不能用(F17)。

两个进程都要回答这个问题:STT 服务在 `/llm/enabled` 里报 `supported` / `reason`,
决定界面上的开关能不能拨;LLM 服务按它挑后端、加载模型。两边的答案必须一致——
否则就会出现「开关能拨,LLM 服务却起不来」或反过来的情况。放在 shared 里,
两边都不必 import 对方(`services.llm_server` 在 import 时就会建引擎、读状态文件)。

- Apple Silicon:MLX(mlx-lm),`pyproject.toml` 按平台 marker 默认就装。
- 其它平台:llama.cpp(llama-cpp-python 跑 GGUF),要 `setup-env --llm` 装 `llm-cpp` extra。
- `VIF_LLM_BACKEND=mlx|llamacpp` 可以强制指定(比如在 Mac 上试 llama.cpp 后端)。
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys

logger = logging.getLogger(__name__)

MLX = "mlx"
LLAMACPP = "llamacpp"
BACKEND_ENV = "VIF_LLM_BACKEND"

# 用户写 llama.cpp / llama_cpp 也认,别因为一个标点就当成没设置。
_ALIASES = {
    "mlx": MLX,
    "llamacpp": LLAMACPP,
    "llama.cpp": LLAMACPP,
    "llama_cpp": LLAMACPP,
    "llama-cpp": LLAMACPP,
    "gguf": LLAMACPP,
}


def has_package(name: str) -> bool:
    """只查「装没装」,不真的 import:llama_cpp / mlx_lm 一 import 就要加载原生库,
    STT 服务报个开关状态犯不着付这个代价。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def setup_hint(platform: str | None = None) -> str:
    """装 llama.cpp 依赖的命令。Windows 用户多半没有 bash,给 .sh 等于没给。"""
    if (platform or sys.platform) == "win32":
        return r"scripts\setup-env.ps1 -Llm"
    return "scripts/setup-env.sh --llm"


def requested_backend() -> str | None:
    """`VIF_LLM_BACKEND` 指定的后端;没设或写错了返回 None(按自动选择)。"""
    raw = os.getenv(BACKEND_ENV, "").strip().lower()
    if not raw:
        return None
    backend = _ALIASES.get(raw)
    if backend is None:
        logger.warning(f"{BACKEND_ENV}={raw!r} 不认识(可选 mlx / llamacpp),按自动选择")
    return backend


def choose_backend(
    apple_silicon: bool,
    requested: str | None = None,
    has=None,
    platform: str | None = None,
) -> tuple[str, str | None]:
    """挑后端。返回 ``(后端名, 不能用的原因)``;原因为 None 表示能用。

    ``has`` 默认是 :func:`has_package`(调用时才取,测试里 monkeypatch 得到)。

    即使哪个都用不了也照样返回一个后端名:LLM 服务要靠它决定 `/models` 列哪张表,
    列出来的应当是「装好依赖之后能用的那些」,而不是一张空表。
    """
    has = has or has_package
    if requested == MLX:
        if apple_silicon:
            return MLX, None
        return MLX, (
            f"{BACKEND_ENV}=mlx 只能在 Apple Silicon 的 Mac 上用;"
            f"去掉这个设置就会改用 llama.cpp 后端"
        )
    if requested == LLAMACPP:
        if has("llama_cpp"):
            return LLAMACPP, None
        # Apple 上 setup-env 的 --llm 不装 llm-cpp(那里默认走 MLX),只能直接点名 extra。
        how = "uv sync --extra llm-cpp" if apple_silicon else setup_hint(platform)
        return (
            LLAMACPP,
            f"{BACKEND_ENV}=llamacpp,但环境里没装 llama-cpp-python。请运行 {how} 安装后重启服务",
        )

    if apple_silicon:
        # Apple Silicon 默认就是 MLX,哪怕在这个进程里找不到 mlx_lm 也不改口说「不支持」:
        # 老的部署里 STT 和 LLM 跑在两个 conda 环境(见 docs/split-architecture.md),
        # STT 这边没有 mlx_lm 是正常的。真缺的话 LLM 服务加载时会把原因报出来。
        # 唯一的例外:这边确实没有 mlx_lm、却装了 llama.cpp,那显然是想用后者。
        if not has("mlx_lm") and has("llama_cpp"):
            return LLAMACPP, None
        return MLX, None
    if has("llama_cpp"):
        return LLAMACPP, None
    return LLAMACPP, (
        f"这台机器上的 LLM 后处理要用 llama.cpp,环境里还没装。"
        f"请运行 {setup_hint(platform)} 安装后重启服务"
    )
