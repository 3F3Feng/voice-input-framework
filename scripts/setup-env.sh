#!/usr/bin/env bash
#
# 一键建环境。探测硬件,挑对应的 PyTorch 后端,交给 uv 装。
#
#   scripts/setup-env.sh                # 自动探测
#   scripts/setup-env.sh --backend cuda # 手动指定 cpu|cuda|rocm|xpu|mlx
#   scripts/setup-env.sh --llm          # 连 LLM 后处理的依赖一起装
#   scripts/setup-env.sh --dev          # 加上测试/lint 工具
#
# 为什么不是 `pip install -r requirements.txt`:
#   PyTorch 给每种加速后端发的是**不同的 wheel,名字都叫 torch**,靠索引地址
#   区分(cpu / cu124 / rocm6.2 / xpu)。requirements.txt 里写不下这个选择,
#   所以以前非 Apple 的机器只能自己手动折腾。pyproject 里用 uv 的
#   [tool.uv.sources] 把「extra → 索引」定死,这个脚本只负责挑 extra。
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKEND=""
WITH_LLM=0
WITH_DEV=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend) BACKEND="${2:?--backend 需要 cpu|cuda|rocm|xpu|mlx}"; shift 2 ;;
    --llm)     WITH_LLM=1; shift ;;
    --dev)     WITH_DEV=1; shift ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1(用 --help 查看用法)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

# ── uv ────────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  say "没找到 uv,安装到 ~/.local/bin ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv 装好了但不在 PATH 里,把 ~/.local/bin 加进 PATH 后重试。"
say "uv $(uv --version | awk '{print $2}')"

# ── 探测硬件 ──────────────────────────────────────────────────────────────
detect_backend() {
  # Apple Silicon:PyPI 上的默认 torch 自带 MPS,另外还能用 MLX
  if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    echo "mlx"; return
  fi
  # NVIDIA
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    echo "cuda"; return
  fi
  # AMD:rocminfo 或 /dev/kfd(ROCm 的内核接口)
  if command -v rocminfo >/dev/null 2>&1 || [[ -e /dev/kfd ]]; then
    echo "rocm"; return
  fi
  # Intel 独显 / Arc:先看有没有 Level Zero 运行时
  if command -v clinfo >/dev/null 2>&1 && clinfo 2>/dev/null | grep -qi "Intel.*Graphics"; then
    echo "xpu"; return
  fi
  echo "cpu"
}

if [[ -z "$BACKEND" ]]; then
  BACKEND="$(detect_backend)"
  say "探测到后端: $BACKEND"
else
  say "手动指定后端: $BACKEND"
fi

# ── 组装 extra ────────────────────────────────────────────────────────────
EXTRAS=()
case "$BACKEND" in
  mlx)
    # Apple Silicon 不需要挑 torch 索引:默认 wheel 就带 MPS。
    # mlx / mlx-lm / mlx-audio 由 pyproject 的平台 marker 自动带上。
    ;;
  cpu|cuda|rocm|xpu) EXTRAS+=(--extra "$BACKEND") ;;
  *) die "不认识的后端「$BACKEND」,可选:cpu cuda rocm xpu mlx" ;;
esac

if [[ $WITH_LLM -eq 1 && "$BACKEND" != "mlx" ]]; then
  # 非 Apple 平台的 LLM 后处理走 llama.cpp;Apple 上用 MLX,不需要额外装
  EXTRAS+=(--extra llm-cpp)
  # PyPI 上的 llama-cpp-python 只有源码包,要现编译。编译失败最常见的原因是缺工具链。
  say "llama-cpp-python 要现编译,需要 CMake 和 C/C++ 编译器(如 build-essential);编译失败先检查这两样。"
fi
[[ $WITH_DEV -eq 1 ]] && EXTRAS+=(--extra dev)

say "uv sync ${EXTRAS[*]:-(无额外 extra)}"
# 不能直接写 "${EXTRAS[@]}":macOS 自带的 /bin/bash 是 3.2,`set -u` 下展开空数组
# 会报「EXTRAS[@]: unbound variable」直接退出 —— Apple Silicon 的默认路径(mlx、
# 不带 --llm / --dev)正好是空数组,一步都走不下去(「更新服务」按钮用的就是系统 bash)。
# `${arr[@]+"${arr[@]}"}` 在 3.2 和新版 bash 上都成立。
uv sync ${EXTRAS[@]+"${EXTRAS[@]}"}

# ── 交代清楚装出来的是什么 ────────────────────────────────────────────────
say "校验..."
uv run python - <<'PY'
import platform
print(f"    Python  : {platform.python_version()} ({platform.machine()})")
try:
    import torch
    print(f"    torch   : {torch.__version__}")
    bits = []
    if torch.backends.mps.is_available():
        bits.append("MPS")
    if torch.cuda.is_available():
        bits.append(f"ROCm({torch.version.hip})" if getattr(torch.version, "hip", None)
                    else f"CUDA({torch.version.cuda})")
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        bits.append("XPU")
    print(f"    加速    : {', '.join(bits) if bits else 'CPU only'}")
except ImportError:
    print("    torch   : 没装(没选硬件 extra?)")
try:
    import mlx.core as mx
    print(f"    MLX     : 可用,设备 {mx.default_device()}")
except Exception as e:
    print(f"    MLX     : 不可用({type(e).__name__})")
try:
    import llama_cpp  # LLM 后处理在非 Apple 平台上的后端(--llm 才装)
    print(f"    llama.cpp: {llama_cpp.__version__}")
except ImportError:
    pass
PY

say "完成。启动服务:"
echo "    uv run python -m services.stt_server"
[[ $WITH_LLM -eq 1 || "$BACKEND" == "mlx" ]] && echo "    uv run python -m services.llm_server"
exit 0
