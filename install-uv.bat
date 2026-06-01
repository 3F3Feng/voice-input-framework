@echo off
REM Voice Input Framework - uv Installation Script (Windows)
REM Fast, lightweight setup using uv package manager
REM
REM Usage:
REM   install-uv.bat              REM Auto-detect and install
REM   install-uv.bat --stt-only   REM STT only
REM   install-uv.bat --llm-only   REM LLM only
REM   install-uv.bat --all        REM Everything
REM   install-uv.bat --dev        REM Include dev dependencies

setlocal enabledelayedexpansion

REM Colors (using ANSI escape codes for Windows 10+)
set "GREEN=[92m"
set "YELLOW=[93m"
set "RED=[91m"
set "BLUE=[94m"
set "CYAN=[96m"
set "NC=[0m"

set "INSTALL_STT=false"
set "INSTALL_LLM=false"
set "INSTALL_ALL=false"
set "INSTALL_DEV=false"
set "SKIP_VENV=false"

REM Parse arguments
:parse_args
if "%~1"=="" goto :check_args
if "%~1"=="--all" (
    set "INSTALL_ALL=true"
    shift
    goto :parse_args
)
if "%~1"=="--stt-only" (
    set "INSTALL_STT=true"
    shift
    goto :parse_args
)
if "%~1"=="--llm-only" (
    set "INSTALL_LLM=true"
    shift
    goto :parse_args
)
if "%~1"=="--dev" (
    set "INSTALL_DEV=true"
    shift
    goto :parse_args
)
if "%~1"=="--no-venv" (
    set "SKIP_VENV=true"
    shift
    goto :parse_args
)
if "%~1"=="--help" (
    goto :usage
)
echo %RED%[ERROR]%NC% Unknown option: %~1
goto :usage

:check_args
if "%INSTALL_STT%"=="false" if "%INSTALL_LLM%"=="false" if "%INSTALL_ALL%"=="false" (
    set "INSTALL_ALL=true"
)

echo ============================================================
echo Voice Input Framework - uv Installation (Windows)
echo ============================================================

REM Detect GPU
set "PLATFORM=cpu"
set "PLATFORM_NAME=CPU (Windows)"
where nvidia-smi >nul 2>&1
if %errorlevel%==0 (
    set "PLATFORM=cuda"
    for /f "tokens=*" %%i in ('nvidia-smi --query-gpu^=name --format^=csv,noheader 2^>nul') do (
        set "GPU_NAME=%%i"
        goto :gpu_found
    )
    :gpu_found
    set "PLATFORM_NAME=NVIDIA GPU (Windows): !GPU_NAME!"
)

echo %BLUE%[INFO]%NC% Platform: !PLATFORM_NAME!

REM Check uv
where uv >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%i in ('uv --version') do set "UV_VER=%%i"
    echo %BLUE%[INFO]%NC% uv found: !UV_VER!
) else (
    echo %CYAN%[STEP]%NC% Installing uv...
    powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if %errorlevel% neq 0 (
        echo %RED%[ERROR]%NC% Failed to install uv
        exit /b 1
    )
    echo %GREEN%[OK]%NC% uv installed
)

REM Check Python
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%i in ('python --version 2^>^&1') do set "PY_VER=%%i"
    echo %BLUE%[INFO]%NC% Python: !PY_VER!
) else (
    echo %RED%[ERROR]%NC% Python not found. Please install Python 3.11+
    exit /b 1
)

echo.

REM Create virtual environment
if "%SKIP_VENV%"=="false" (
    if exist ".venv" (
        echo %BLUE%[INFO]%NC% Virtual environment already exists
    ) else (
        echo %CYAN%[STEP]%NC% Creating virtual environment (Python 3.11)...
        uv venv .venv --python 3.11
        if %errorlevel% neq 0 (
            echo %RED%[ERROR]%NC% Failed to create virtual environment
            exit /b 1
        )
        echo %GREEN%[OK]%NC% Virtual environment created
    )
    
    REM Activate virtual environment
    call .venv\Scripts\activate.bat
)

REM Install base
echo %CYAN%[STEP]%NC% Installing base dependencies...
uv pip install -r requirements\base.txt
echo %GREEN%[OK]%NC% Base dependencies installed

REM Install STT
if "%INSTALL_ALL%"=="true" goto :install_stt
if "%INSTALL_STT%"=="true" goto :install_stt
goto :skip_stt

:install_stt
echo %CYAN%[STEP]%NC% Installing STT dependencies for !PLATFORM_NAME!...

if "!PLATFORM!"=="cuda" (
    echo %BLUE%[INFO]%NC% Installing PyTorch with CUDA support...
    uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
    uv pip install -r requirements\stt-cuda.txt
) else (
    uv pip install -r requirements\stt-cpu.txt
)
echo %GREEN%[OK]%NC% STT dependencies installed
:skip_stt

REM Install LLM
if "%INSTALL_ALL%"=="true" goto :install_llm
if "%INSTALL_LLM%"=="true" goto :install_llm
goto :skip_llm

:install_llm
echo %CYAN%[STEP]%NC% Installing LLM dependencies for !PLATFORM_NAME!...

if "!PLATFORM!"=="cuda" (
    REM PyTorch should already be installed from STT
    uv pip install -r requirements\llm-cuda.txt
) else (
    echo %YELLOW%[WARN]%NC% LLM is not recommended on CPU (no GPU acceleration)
    echo %BLUE%[INFO]%NC% Skipping LLM installation
)
echo %GREEN%[OK]%NC% LLM dependencies installed
:skip_llm

REM Install dev
if "%INSTALL_DEV%"=="true" (
    echo %CYAN%[STEP]%NC% Installing development dependencies...
    uv pip install pytest pytest-asyncio ruff
    echo %GREEN%[OK]%NC% Dev dependencies installed
)

echo.
echo ============================================================
echo %GREEN%[OK]%NC% Installation complete!
echo ============================================================
echo.
echo Next steps:

if "%SKIP_VENV%"=="false" (
    echo   1. Activate environment:
    echo      .venv\Scripts\activate
)

echo   2. Start STT server:  python -m services.stt_server
echo   3. Start LLM server:  python -m services.llm_server
echo   4. Run client:        python run_client.py
echo.

REM Show environment size
if exist ".venv" (
    for /f "tokens=*" %%i in ('powershell -Command "(Get-ChildItem -Recurse .venv | Measure-Object -Property Length -Sum).Sum / 1MB"') do (
        echo Environment size: %%i MB
    )
)

goto :end

:usage
echo Voice Input Framework - uv Installation Script (Windows)
echo.
echo Usage: %~nx0 [OPTIONS]
echo.
echo Options:
echo   --all         Install all dependencies (STT + LLM)
echo   --stt-only    Install STT dependencies only
echo   --llm-only    Install LLM dependencies only
echo   --dev         Include development dependencies
echo   --no-venv     Skip virtual environment creation
echo   --help        Show this help message
echo.
echo Examples:
echo   %~nx0                REM Auto-detect and install
echo   %~nx0 --all          REM Install everything
echo   %~nx0 --stt-only     REM STT only

:end
endlocal
