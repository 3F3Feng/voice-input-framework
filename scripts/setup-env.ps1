<#
.SYNOPSIS
  一键建环境(Windows)。探测硬件,挑对应的 PyTorch 后端,交给 uv 装。

.DESCRIPTION
  scripts/setup-env.sh 的 PowerShell 版,行为保持一致:

    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1               # 自动探测
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -Backend cuda # 手动指定 cpu|cuda|xpu
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -Llm          # 连 LLM 后处理的依赖一起装
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -Dev          # 加上测试/lint 工具

  为什么不是 `pip install -r requirements.txt`:
    PyTorch 给每种加速后端发的是不同的 wheel,名字都叫 torch,靠索引地址区分
    (cpu / cu124 / xpu)。requirements.txt 里写不下这个选择。pyproject 里用 uv 的
    [tool.uv.sources] 把「extra -> 索引」定死,这个脚本只负责挑 extra。

  以前 Windows 上只能在 Git Bash 里跑 setup-env.sh,多数 Windows 用户没有 bash,
  README 那一句基本等于「Windows 请自己想办法」。

  注意:本文件必须存成带 BOM 的 UTF-8。Windows PowerShell 5.1 读没有 BOM 的脚本时
  按系统代码页(中文系统是 GBK)解码,中文字符串会乱码,甚至整个脚本解析失败。
#>
[CmdletBinding()]
param(
    # cpu | cuda | xpu。留空自动探测。ROCm 的 PyTorch 只有 Linux 版,MLX 只有 Apple Silicon。
    [string]$Backend = "",
    [switch]$Llm,
    [switch]$Dev,
    [switch]$Help
)

# 不设 $ErrorActionPreference = "Stop":Windows PowerShell 5.1 在 Stop 下,只要原生命令
# (uv、nvidia-smi)的 stderr 被重定向,写一行进度就会被当成异常终止脚本 —— 而 uv 的
# 下载进度全在 stderr 上。原生命令一律看 $LASTEXITCODE。

# 控制台按 UTF-8 输出,否则下面校验时 Python 打印的中文在默认代码页下是乱码。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = "utf-8"

function Say([string]$msg)  { Write-Host "==> " -ForegroundColor Cyan -NoNewline; Write-Host $msg }
function Warn([string]$msg) { Write-Host "[警告] " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Die([string]$msg)  { Write-Host "[错误] " -ForegroundColor Red -NoNewline; Write-Host $msg; exit 1 }

if ($Help) {
    Get-Help $PSCommandPath -Detailed
    exit 0
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot -ErrorAction Stop

# ── uv ────────────────────────────────────────────────────────────────────
function Test-Uv { [bool](Get-Command uv -ErrorAction SilentlyContinue) }

if (-not (Test-Uv)) {
    Say "没找到 uv,安装到 $HOME\.local\bin ..."
    # 官方安装脚本。它会改用户级 PATH,但只对新开的终端生效,所以本次会话里手动补上。
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if ($LASTEXITCODE -ne 0) { Die "uv 安装失败(退出码 $LASTEXITCODE)。可以手动安装:https://docs.astral.sh/uv/" }
    $env:Path = "$HOME\.local\bin;$HOME\.cargo\bin;$env:Path"
}
if (-not (Test-Uv)) { Die "uv 装好了但不在 PATH 里,把 $HOME\.local\bin 加进 PATH 后重试。" }
$uvVersion = (& uv --version) -replace '^uv\s+', ''
Say "uv $uvVersion"

# ── 探测硬件 ──────────────────────────────────────────────────────────────
function Get-DetectedBackend {
    # NVIDIA:nvidia-smi 随驱动一起装。光有命令不够,-L 能列出显卡才算数
    # (驱动坏了 / 显卡被禁用时命令还在,但会报错)。
    # 探测失败(驱动坏了之类)只意味着不选 cuda,不该让整个脚本退出,所以包一层 try。
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        try {
            & nvidia-smi -L *> $null
            if ($LASTEXITCODE -eq 0) { return "cuda" }
        } catch { }
    }
    # AMD(ROCm 的 PyTorch 只有 Linux 版)和 Intel 独显不自动选:探测不可靠,
    # 选错了反而装一个跑不起来的 torch。Intel Arc 可以手动 -Backend xpu。
    return "cpu"
}

if ([string]::IsNullOrWhiteSpace($Backend)) {
    $Backend = Get-DetectedBackend
    Say "探测到后端: $Backend"
} else {
    $Backend = $Backend.Trim().ToLower()
    Say "手动指定后端: $Backend"
}

# ── 组装 extra ────────────────────────────────────────────────────────────
$extras = @()
switch ($Backend) {
    { $_ -in @("cpu", "cuda", "xpu") } { $extras += @("--extra", $Backend) }
    "rocm" { Die "ROCm 版 PyTorch 只有 Linux 版。Windows 上的 AMD 显卡请用 -Backend cpu。" }
    "mlx"  { Die "MLX 只能在 Apple Silicon 的 macOS 上用。" }
    default { Die "不认识的后端「$Backend」,可选:cpu cuda xpu" }
}

if ($Llm) {
    # 非 Apple 平台的 LLM 后处理走 llama.cpp
    $extras += @("--extra", "llm-cpp")
    # PyPI 上的 llama-cpp-python 只有源码包,要现编译。编译失败最常见的原因是没装 C++ 工具链。
    Say "llama-cpp-python 要现编译,需要 CMake 和 Visual Studio Build Tools(C++ 桌面开发);编译失败先检查这两样。"
}
if ($Dev) { $extras += @("--extra", "dev") }

$shown = if ($extras.Count -gt 0) { $extras -join " " } else { "(无额外 extra)" }
Say "uv sync $shown"
& uv sync @extras
if ($LASTEXITCODE -ne 0) { Die "uv sync 失败(退出码 $LASTEXITCODE),见上面的输出。" }

# ── 交代清楚装出来的是什么 ────────────────────────────────────────────────
Say "校验..."
$verify = @'
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
'@
# 写成临时文件再跑,而不是经管道喂给 `python -`:Windows PowerShell 5.1 往原生命令的
# stdin 写字符串时按 $OutputEncoding(默认 ASCII)编码,中文会变成问号。
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "vif-setup-verify-$PID.py"
[System.IO.File]::WriteAllText($tmp, $verify, (New-Object System.Text.UTF8Encoding($false)))
try {
    & uv run python $tmp
    if ($LASTEXITCODE -ne 0) { Warn "校验脚本没跑完(退出码 $LASTEXITCODE),环境可能有问题。" }
} finally {
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

Say "完成。启动服务:"
Write-Host "    uv run python -m services.stt_server"
if ($Llm) { Write-Host "    uv run python -m services.llm_server" }
Write-Host ""
Write-Host "    或者在客户端「设置 → 服务 → 本地管理」里点「自动探测」,"
Write-Host "    会找到 $RepoRoot\.venv\Scripts\python.exe,再点「启动」。"
exit 0
