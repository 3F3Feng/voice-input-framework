<#
.SYNOPSIS
  一键建环境(Windows)。探测硬件,挑对应的 PyTorch 后端,交给 uv 装。

.DESCRIPTION
  scripts/setup-env.sh 的 PowerShell 版,行为保持一致:

    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1               # 自动探测
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -Backend cuda # 手动指定 cpu|cuda|xpu
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -LlmBackend cpu  # llama.cpp 手动指定 cuda|vulkan|cpu(默认自动)
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1 -NoLlm        # 不装 llama.cpp(那样量化版 Qwen3-ASR 和后处理都用不了)
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
    # llama.cpp 默认就装:语音识别(量化版 Qwen3-ASR)和 LLM 后处理都跑在它上面,不是可选的
    # 附加功能了。-Llm 留着只为兼容以前的用法,-NoLlm 才是不装。
    [switch]$Llm,
    [switch]$NoLlm,
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

# ── Python:用 uv 自己管理的那一份 ─────────────────────────────────────────
#
# uv 默认会用机器上已有的 Python。Windows 上这会出事:Anaconda 的 Python 目录里自带一份
# 旧的 Visual C++ 运行库(msvcp140.dll),DLL 的查找顺序里它排在 System32 前面,llama.cpp
# 的预编译包加载到它就崩(access violation reading 0x0,见 shared\llama_runtime.py)。
# uv 管理的 Python 目录里没有这个文件,用的是系统那份。没装过时 uv 会自己下载(约 20 MB)。
# 想用别的 Python 可以自己设 UV_PYTHON_PREFERENCE / UV_PYTHON。
if (-not $env:UV_PYTHON_PREFERENCE -and -not $env:UV_PYTHON) {
    $env:UV_PYTHON_PREFERENCE = "only-managed"
    $venvCfg = Join-Path $RepoRoot ".venv\pyvenv.cfg"
    if (Test-Path $venvCfg) {
        $pyHome = ""
        foreach ($line in (Get-Content $venvCfg)) {
            if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { $pyHome = $Matches[1] }
        }
        $managedDir = ""
        try { $managedDir = "$(& uv python dir 2>$null)".Trim() } catch { }
        if ($pyHome -and $managedDir -and -not $pyHome.StartsWith($managedDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            Say "现有的 .venv 建在 $pyHome 的 Python 上,这次会换成 uv 自己管理的 Python 重建(装过的包从 uv 的缓存里装)。"
        }
    }
}

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
# 进程加载到的 Visual C++ 运行库太旧时,库加载得了,一调用就崩(读空指针)。三种版本都
# 一样,所以单独报出来(退出码 5),脚本不再挨个试下去。
runtime = llama_runtime.msvc_runtime()
if runtime is not None and runtime.too_old:
    need = ".".join(str(n) for n in llama_runtime.MIN_MSVC_RUNTIME)
    print(f"    Visual C++ 运行库太旧:{runtime.path} 是 {runtime.version_text},llama.cpp 要 {need} 以上。")
    if runtime.from_system:
        print("    更新它(微软官方,安装时会要管理员权限),再重跑本脚本:")
        print("        winget install --id Microsoft.VCRedist.2015+.x64 -e")
        print("        或者下载安装 https://aka.ms/vs/17/release/vc_redist.x64.exe")
    else:
        print("    这一份是 Python 安装自带的(Anaconda 之类的发行版会带),排在系统那份前面。")
        print("    本脚本默认用 uv 自己管理的 Python 来避开它;去掉 UV_PYTHON / UV_PYTHON_PREFERENCE 后重跑。")
    sys.exit(5)
try:
    gpu = bool(llama_cpp.llama_supports_gpu_offload())
except OSError as e:  # ctypes 把库里的崩溃转成 OSError
    print(f"    llama.cpp 加载了,但一调用就出错:{e}")
    sys.exit(6)
print(f"    llama.cpp {llama_cpp.__version__}:{'认出了显卡' if gpu else '没有可用的显卡,只能用 CPU'}")
sys.exit(0 if (gpu or want == "cpu") else 4)
'@

# 装上之后验证:能 import,而且(显卡版)llama.cpp 真的认出了一块显卡。返回校验脚本的
# 退出码:0 可用,5 是系统的 Visual C++ 运行库太旧,别的都是「这一种用不了」。
# 脚本写成临时文件再跑(原因见下面校验那一段)。
function Test-Llm([string]$name) {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) "vif-setup-llm-$PID.py"
    [System.IO.File]::WriteAllText($tmp, $verifyLlm, (New-Object System.Text.UTF8Encoding($false)))
    try {
        # | Out-Host 不能省:函数里原生命令的输出会并进函数的返回值,那样返回的就是
        # 「几行字 + 退出码」的数组,拿它做判断就错了。
        & uv run --no-sync python $tmp $name $RepoRoot | Out-Host
        return $LASTEXITCODE
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

$LlmBackend = $LlmBackend.Trim().ToLower()
$wantLlm = -not $NoLlm
$llmCandidates = @()
if ($wantLlm) {
    switch ($LlmBackend) {
        "auto" {
            switch (Get-DetectedLlmBackend) {
                "cuda"   { $llmCandidates = @("cuda", "vulkan", "cpu") }
                "vulkan" { $llmCandidates = @("vulkan", "cpu") }
                default  { $llmCandidates = @("cpu") }
            }
            Say "llama.cpp(语音识别 + LLM 后处理):自动选择,依次尝试 $($llmCandidates -join ' ')"
        }
        { $_ -in @("cuda", "vulkan", "cpu") } {
            $llmCandidates = @($LlmBackend)
            Say "llama.cpp(语音识别 + LLM 后处理):手动指定 $LlmBackend"
        }
        default { Die "不认识的 -LlmBackend「$LlmBackend」,可选:auto cuda vulkan cpu" }
    }
}

$llmInstalled = ""
$vcRuntimeTooOld = $false
foreach ($candidate in $llmCandidates) {
    # --reinstall-package 不能省:三种版本是同一个包名、同一个版本号,只是来自不同的索引。
    # 不强制重装的话,uv 看到已经装着这个版本就什么都不做,换版本等于没换。
    $syncArgs = @($extras) + @("--extra", (Get-LlmExtra $candidate), "--reinstall-package", "llama-cpp-python")
    Say "uv sync $($syncArgs -join ' ')"
    & uv sync @syncArgs
    if ($LASTEXITCODE -eq 0) {
        $verified = Test-Llm $candidate
        if ($verified -eq 0) {
            $llmInstalled = $candidate
            break
        }
        if ($verified -eq 5) {
            # 哪一种版本都要同一个运行库,再试下去只是把同一个错再报两遍
            $vcRuntimeTooOld = $true
            break
        }
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
if (Test-Path $venvDir) {
    $installedName = if ($llmInstalled) { $llmInstalled } else { "none" }
    $requestedName = if ($wantLlm) { $LlmBackend } else { "off" }
    $marker = '{"requested": "' + $requestedName + '", "installed": "' + $installedName + '"}'
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

if ($wantLlm) {
    if ($vcRuntimeTooOld) {
        Warn "llama.cpp(llama-cpp-python)没装上:它加载到的 Visual C++ 运行库太旧,怎么解决见上面的输出。"
        Warn "Whisper 系的识别模型不受影响;量化版 Qwen3-ASR 和 LLM 后处理用不了。"
    } elseif (-not $llmInstalled) {
        Warn "llama.cpp(llama-cpp-python)没装上,原因见上面的输出。Whisper 系的识别模型不受影响;"
        Warn "量化版 Qwen3-ASR 和 LLM 后处理用不了。多半是连不上 github.com(预编译包放在那里),稍后重跑本脚本即可。"
    } elseif ($llmInstalled -eq "cpu") {
        if ($llmCandidates[0] -ne "cpu") {
            Warn "llama.cpp 装的是 CPU 版(显卡版在这台机器上用不了):能用,但会慢。"
            Warn "更新显卡驱动后重跑本脚本,会重新尝试显卡版。"
        } elseif ($LlmBackend -eq "cpu") {
            Say "llama.cpp:CPU 版(手动指定)。"
        } else {
            Say "llama.cpp:CPU 版(没探测到独立显卡)。"
        }
    } else {
        Say "llama.cpp:$llmInstalled 版(显卡加速)。"
    }
}
Say "完成。启动服务:"
Write-Host "    uv run python -m services.stt_server"
if ($llmInstalled) { Write-Host "    uv run python -m services.llm_server" }
Write-Host ""
Write-Host "    或者在客户端「设置 → 服务 → 本地管理」里点「自动探测」,"
Write-Host "    会找到 $RepoRoot\.venv\Scripts\python.exe,再点「启动」。"
exit 0
