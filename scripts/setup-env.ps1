<#
.SYNOPSIS
  一键建环境(Windows)。探测硬件,挑对应的 PyTorch 后端,交给 uv 装。

.DESCRIPTION
  scripts/setup-env.sh 的 PowerShell 版,行为保持一致:

    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1               # 自动探测
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -Backend cuda # 手动指定 cpu|cuda|xpu
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -Llm          # 连 LLM 后处理的依赖一起装(有显卡就装显卡版)
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -Llm -LlmBackend cpu  # 手动指定 cuda|vulkan|cpu
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
    # llama.cpp 装哪一种:auto | cuda | vulkan | cpu。auto = 有显卡就装显卡版,不行再退回。
    [string]$LlmBackend = "auto",
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

if ($Dev) { $extras += @("--extra", "dev") }

# ── LLM 后处理(llama.cpp)装哪一种 ─────────────────────────────────────────
#
# llama-cpp-python 按硬件有三种预编译版(pyproject 里的 llm-cpp-cuda / llm-cpp-vulkan /
# llm-cpp),都不需要 Visual Studio Build Tools(以前从 PyPI 的源码包现编译,没装 C++
# 工具链的 Windows 上必失败)。默认模型在 CPU 上一句话要等好几秒,所以有显卡就装
# 显卡版,CPU 版只是兜底:
#   NVIDIA      -> cuda(最快;CUDA 运行库用 PyTorch CUDA 版带的那份,见
#                  shared\llama_runtime.py),不行退 vulkan,再退 cpu
#   别的显卡    -> vulkan(AMD / Intel Arc;只要显卡驱动),不行退 cpu
#   没有显卡    -> cpu
# 「不行」指的是装上之后真的加载一次,看 llama.cpp 认不认得出显卡(驱动太旧、没有
# Vulkan 运行库时包装得上但用不了)。-LlmBackend 手动指定时只试那一种。
function Get-DetectedLlmBackend {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        try {
            & nvidia-smi -L *> $null
            if ($LASTEXITCODE -eq 0) { return "cuda" }
        } catch { }
    }
    # 集成显卡不算:Intel 核显跑 Vulkan 并不比 CPU 快。查不出来就当没有显卡。
    try {
        $names = (Get-CimInstance Win32_VideoController -ErrorAction Stop | ForEach-Object { $_.Name }) -join "; "
        if ($names -match 'NVIDIA|GeForce|Radeon RX|Radeon Pro|Radeon \(TM\) RX|Intel\(R\) Arc|\bArc\b') { return "vulkan" }
    } catch { }
    return "cpu"
}

function Get-LlmExtra([string]$name) {
    switch ($name) {
        "cuda"   { return "llm-cpp-cuda" }
        "vulkan" { return "llm-cpp-vulkan" }
        default  { return "llm-cpp" }
    }
}

$verifyLlm = @'
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
'@

# 装上之后验证:能 import,而且(显卡版)llama.cpp 真的认出了一块显卡。
# 脚本写成临时文件再跑(原因见下面校验那一段)。
function Test-Llm([string]$name) {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) "vif-setup-llm-$PID.py"
    [System.IO.File]::WriteAllText($tmp, $verifyLlm, (New-Object System.Text.UTF8Encoding($false)))
    try {
        # | Out-Host 不能省:函数里原生命令的输出会并进函数的返回值,那样返回的就是
        # 「几行字 + $true」的数组,怎么判断都是真。
        & uv run --no-sync python $tmp $name $RepoRoot | Out-Host
        return ($LASTEXITCODE -eq 0)
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

$LlmBackend = $LlmBackend.Trim().ToLower()
$llmCandidates = @()
if ($Llm) {
    switch ($LlmBackend) {
        "auto" {
            switch (Get-DetectedLlmBackend) {
                "cuda"   { $llmCandidates = @("cuda", "vulkan", "cpu") }
                "vulkan" { $llmCandidates = @("vulkan", "cpu") }
                default  { $llmCandidates = @("cpu") }
            }
            Say "LLM 后处理(llama.cpp):自动选择,依次尝试 $($llmCandidates -join ' ')"
        }
        { $_ -in @("cuda", "vulkan", "cpu") } {
            $llmCandidates = @($LlmBackend)
            Say "LLM 后处理(llama.cpp):手动指定 $LlmBackend"
        }
        default { Die "不认识的 -LlmBackend「$LlmBackend」,可选:auto cuda vulkan cpu" }
    }
}

$llmInstalled = ""
foreach ($candidate in $llmCandidates) {
    # --reinstall-package 不能省:三种版本是同一个包名、同一个版本号,只是来自不同的索引。
    # 不强制重装的话,uv 看到已经装着这个版本就什么都不做,换版本等于没换。
    $syncArgs = @($extras) + @("--extra", (Get-LlmExtra $candidate), "--reinstall-package", "llama-cpp-python")
    Say "uv sync $($syncArgs -join ' ')"
    & uv sync @syncArgs
    if ($LASTEXITCODE -eq 0 -and (Test-Llm $candidate)) {
        $llmInstalled = $candidate
        break
    }
    Warn "llama.cpp 的 $candidate 版在这台机器上用不了(原因见上)。"
}
if (-not $llmInstalled) {
    # 没要 LLM 依赖,或者哪一种都没装上:把其余的装好。识别本身不依赖它,不能让整个
    # 环境跟着建不起来。
    $shown = if ($extras.Count -gt 0) { $extras -join " " } else { "(无额外 extra)" }
    Say "uv sync $shown"
    & uv sync @extras
    if ($LASTEXITCODE -ne 0) { Die "uv sync 失败(退出码 $LASTEXITCODE),见上面的输出。" }
}

# 记下这次是怎么选的:「更新服务」重跑本脚本时,手动指定过的要原样带上,自动选的
# 重新探测(换了显卡、装了驱动之后能自己升上去)。
$venvDir = Join-Path $RepoRoot ".venv"
if ($Llm -and (Test-Path $venvDir)) {
    $installedName = if ($llmInstalled) { $llmInstalled } else { "none" }
    $marker = '{"requested": "' + $LlmBackend + '", "installed": "' + $installedName + '"}'
    [System.IO.File]::WriteAllText((Join-Path $venvDir "vif-llm-backend"), $marker + "`n", (New-Object System.Text.UTF8Encoding($false)))
}

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
'@
# 写成临时文件再跑,而不是经管道喂给 `python -`:Windows PowerShell 5.1 往原生命令的
# stdin 写字符串时按 $OutputEncoding(默认 ASCII)编码,中文会变成问号。
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "vif-setup-verify-$PID.py"
[System.IO.File]::WriteAllText($tmp, $verify, (New-Object System.Text.UTF8Encoding($false)))
try {
    & uv run --no-sync python $tmp
    if ($LASTEXITCODE -ne 0) { Warn "校验脚本没跑完(退出码 $LASTEXITCODE),环境可能有问题。" }
} finally {
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

if ($Llm) {
    if (-not $llmInstalled) {
        Warn "LLM 后处理的依赖(llama-cpp-python)没装上,原因见上面的输出;语音识别不受影响。"
        Warn "多半是连不上 github.com(预编译包放在那里)。可以稍后重跑本脚本,或关掉「LLM 后处理」。"
    } elseif ($llmInstalled -eq "cpu") {
        if ($llmCandidates[0] -ne "cpu") {
            Warn "LLM 后处理装的是 CPU 版(显卡版在这台机器上用不了):能用,但一句话要等好几秒。"
            Warn "更新显卡驱动后重跑本脚本,会重新尝试显卡版。"
        } elseif ($LlmBackend -eq "cpu") {
            Say "LLM 后处理:CPU 版(手动指定;一句话要等好几秒)。"
        } else {
            Say "LLM 后处理:CPU 版(没探测到独立显卡;一句话要等好几秒)。"
        }
    } else {
        Say "LLM 后处理:$llmInstalled 版(显卡加速)。"
    }
}
Say "完成。启动服务:"
Write-Host "    uv run python -m services.stt_server"
if ($llmInstalled) { Write-Host "    uv run python -m services.llm_server" }
Write-Host ""
Write-Host "    或者在客户端「设置 → 服务 → 本地管理」里点「自动探测」,"
Write-Host "    会找到 $RepoRoot\.venv\Scripts\python.exe,再点「启动」。"
exit 0
