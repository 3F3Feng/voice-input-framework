#!/usr/bin/env bash
#
# 一键建环境。探测硬件,挑对应的 PyTorch 后端,交给 uv 装。
#
#   scripts/setup-env.sh                # 自动探测
#   scripts/setup-env.sh --backend cuda # 手动指定 cpu|cuda|rocm|xpu|mlx
#   scripts/setup-env.sh --llm-backend cpu   # llama.cpp 手动指定 cuda|vulkan|cpu(默认自动)
#   scripts/setup-env.sh --no-llm       # 不装 llama.cpp(那样量化版 Qwen3-ASR 和后处理都用不了)
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
# llama.cpp 默认就装:非 Apple 平台上语音识别(量化版 Qwen3-ASR)和 LLM 后处理都跑在它
# 上面,不是可选的附加功能了。--llm 留着只为兼容以前的用法。
WITH_LLM=1
LLM_BACKEND="auto"
WITH_DEV=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend) BACKEND="${2:?--backend 需要 cpu|cuda|rocm|xpu|mlx}"; shift 2 ;;
    --llm)     WITH_LLM=1; shift ;;
    --no-llm)  WITH_LLM=0; shift ;;
    --llm-backend) LLM_BACKEND="${2:?--llm-backend 需要 auto|cuda|vulkan|cpu}"; WITH_LLM=1; shift 2 ;;
    --dev)     WITH_DEV=1; shift ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

[[ $WITH_DEV -eq 1 ]] && EXTRAS+=(--extra dev)

# ── LLM 后处理(llama.cpp)装哪一种 ─────────────────────────────────────────
#
# llama-cpp-python 按硬件有三种预编译版(pyproject 里的 llm-cpp-cuda / llm-cpp-vulkan /
# llm-cpp)。默认模型在 CPU 上一句话要等好几秒,所以**有显卡就装显卡版**,CPU 版只是
# 兜底:
#   NVIDIA      → cuda(最快;CUDA 运行库用 PyTorch CUDA 版带的那份,见
#                 shared/llama_runtime.py),不行退 vulkan,再退 cpu
#   别的显卡    → vulkan(AMD / Intel Arc;只要显卡驱动),不行退 cpu
#   没有显卡    → cpu
# 「不行」指的是装上之后真的加载一次,看 llama.cpp 认不认得出显卡(驱动太旧、没有
# Vulkan 运行库时包装得上但用不了)。--llm-backend 手动指定时只试那一种。
#
# Apple Silicon 用 MLX,不走这里。
detect_llm_backend() {
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    echo "cuda"; return
  fi
  # 集成显卡不算:Intel 核显跑 Vulkan 并不比 CPU 快。lspci 没有就当没有显卡。
  if command -v lspci >/dev/null 2>&1 \
    && lspci 2>/dev/null | grep -Ei 'vga|3d|display' | grep -Eiq 'nvidia|amd|radeon|intel.*arc'; then
    echo "vulkan"; return
  fi
  echo "cpu"
}

llm_extra() {  # 后端名 → pyproject 里的 extra
  case "$1" in
    cuda)   echo "llm-cpp-cuda" ;;
    vulkan) echo "llm-cpp-vulkan" ;;
    cpu)    echo "llm-cpp" ;;
  esac
}

# 装上之后验证:能 import,而且(显卡版)llama.cpp 真的认出了一块显卡。
verify_llm() {  # $1 = cuda|vulkan|cpu
  uv run --no-sync python - "$1" "$REPO_ROOT" <<'PY'
import sys
want = sys.argv[1]
sys.path.insert(0, sys.argv[2])
try:
    # CUDA 版要的 CUDA 运行库在 PyTorch 的目录里,和 LLM 服务用同一段代码指给它
    from shared import llama_runtime
    llama_runtime.prepare()
    import llama_cpp
except Exception as e:  # 缺驱动 / 缺 Vulkan 运行库时在这里就失败
    print(f"    llama.cpp 加载失败:{type(e).__name__}: {e}")
    sys.exit(3)
gpu = bool(llama_cpp.llama_supports_gpu_offload())
print(f"    llama.cpp {llama_cpp.__version__}:{'认出了显卡' if gpu else '没有可用的显卡,只能用 CPU'}")
sys.exit(0 if (gpu or want == "cpu") else 4)
PY
}

WANT_LLM=0
LLM_CANDIDATES=()
if [[ $WITH_LLM -eq 1 && "$BACKEND" != "mlx" ]]; then
  WANT_LLM=1
  case "$LLM_BACKEND" in
    auto)
      case "$(detect_llm_backend)" in
        cuda)   LLM_CANDIDATES=(cuda vulkan cpu) ;;
        vulkan) LLM_CANDIDATES=(vulkan cpu) ;;
        *)      LLM_CANDIDATES=(cpu) ;;
      esac
      say "llama.cpp(语音识别 + LLM 后处理):自动选择,依次尝试 ${LLM_CANDIDATES[*]}" ;;
    cuda|vulkan|cpu)
      LLM_CANDIDATES=("$LLM_BACKEND")
      say "llama.cpp(语音识别 + LLM 后处理):手动指定 $LLM_BACKEND" ;;
    *) die "不认识的 --llm-backend「$LLM_BACKEND」,可选:auto cuda vulkan cpu" ;;
  esac
fi

# 不能直接写 "${EXTRAS[@]}":macOS 自带的 /bin/bash 是 3.2,`set -u` 下展开空数组
# 会报「EXTRAS[@]: unbound variable」直接退出 —— Apple Silicon 的默认路径(mlx、
# 不带 --llm / --dev)正好是空数组,一步都走不下去(「更新服务」按钮用的就是系统 bash)。
# `${arr[@]+"${arr[@]}"}` 在 3.2 和新版 bash 上都成立。
LLM_INSTALLED=""
if [[ $WANT_LLM -eq 1 ]]; then
  for candidate in "${LLM_CANDIDATES[@]}"; do
    say "uv sync ${EXTRAS[*]:-} --extra $(llm_extra "$candidate")"
    # --reinstall-package 不能省:三种版本是**同一个包名、同一个版本号**,只是来自不同
    # 的索引。不强制重装的话,uv 看到已经装着这个版本就什么都不做,换版本等于没换。
    if uv sync ${EXTRAS[@]+"${EXTRAS[@]}"} --extra "$(llm_extra "$candidate")" \
        --reinstall-package llama-cpp-python \
      && verify_llm "$candidate"; then
      LLM_INSTALLED="$candidate"
      break
    fi
    warn "llama.cpp 的 $candidate 版在这台机器上用不了(原因见上)。"
  done
fi
if [[ -z "$LLM_INSTALLED" ]]; then
  # 没要 LLM 依赖,或者哪一种都没装上:把其余的装好。识别本身不依赖它,不能让整个
  # 环境跟着建不起来。
  say "uv sync ${EXTRAS[*]:-(无额外 extra)}"
  uv sync ${EXTRAS[@]+"${EXTRAS[@]}"} || die "uv sync 失败,见上面的输出。"
fi

# 记下这次是怎么选的:「更新服务」重跑本脚本时,手动指定过的(包括 --no-llm)要原样
# 带上,自动选的重新探测(换了显卡、装了驱动之后能自己升上去)。
if [[ -d .venv && "$BACKEND" != "mlx" ]]; then
  if [[ $WANT_LLM -eq 1 ]]; then
    printf '{"requested": "%s", "installed": "%s"}\n' "$LLM_BACKEND" "${LLM_INSTALLED:-none}" \
      > .venv/vif-llm-backend
  else
    printf '{"requested": "off", "installed": "none"}\n' > .venv/vif-llm-backend
  fi
fi

# ── 交代清楚装出来的是什么 ────────────────────────────────────────────────
say "校验..."
uv run --no-sync python - <<'PY'
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
PY

if [[ $WANT_LLM -eq 1 ]]; then
  case "$LLM_INSTALLED" in
    "")
      warn "llama.cpp(llama-cpp-python)没装上,原因见上面的输出。Whisper 系的识别模型不受影响;"
      warn "量化版 Qwen3-ASR 和 LLM 后处理用不了。多半是连不上 github.com(预编译包放在那里),稍后重跑本脚本即可。" ;;
    cpu)
      if [[ "${LLM_CANDIDATES[0]}" != "cpu" ]]; then
        warn "llama.cpp 装的是 CPU 版(显卡版在这台机器上用不了):能用,但会慢。"
        warn "更新显卡驱动后重跑本脚本,会重新尝试显卡版。"
      elif [[ "$(uname -s)" == "Darwin" ]]; then
        # macOS 上只有这一种包(从源码编译),自带 Metal 显卡加速。
        say "llama.cpp:macOS,Metal 显卡加速。"
      elif [[ "$LLM_BACKEND" == "cpu" ]]; then
        say "llama.cpp:CPU 版(手动指定)。"
      else
        say "llama.cpp:CPU 版(没探测到独立显卡)。"
      fi ;;
    *) say "llama.cpp:$LLM_INSTALLED 版(显卡加速)。" ;;
  esac
fi
say "完成。启动服务:"
echo "    uv run python -m services.stt_server"
[[ -n "$LLM_INSTALLED" || "$BACKEND" == "mlx" ]] && echo "    uv run python -m services.llm_server"
exit 0
