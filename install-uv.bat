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

set "INSTALL_STT=false"
set "INSTALL_LLM=false"
set "INSTALL_ALL=false"
set "INSTALL_DEV=false"
set "SKIP_VENV=false"
set "PLATFORM=cpu"
set "PLATFORM_NAME=CPU (Windows)"

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
echo [ERROR] Unknown option: %~1
goto :usage

:check_args
if "%INSTALL_STT%"=="false" if "%INSTALL_LLM%"=="false" if "%INSTALL_ALL%"=="false" (
    set "INSTALL_ALL=true"
)

echo ============================================================
echo Voice Input Framework - uv Installation (Windows)
echo ============================================================

REM Detect GPU
where nvidia-smi >nul 2>&1
if %errorlevel%==0 (
    set "PLATFORM=cuda"
    for /f "delims=" %%i in ('nvidia-smi --query-gpu^=name --format^=csv,noheader 2^>nul') do (
        set "GPU_NAME=%%i"
        goto :gpu_done
    )
    :gpu_done
    if defined GPU_NAME (
        set "PLATFORM_NAME=NVIDIA GPU (Windows): !GPU_NAME!"
    ) else (
        set "PLATFORM_NAME=NVIDIA GPU (Windows)"
    )
)

echo [INFO] Platform: %PLATFORM_NAME%

REM Check uv
where uv >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%i in ('uv --version') do set "UV_VER=%%i"
    echo [INFO] uv found: !UV_VER!
) else (
    echo [STEP] Installing uv...
    powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install uv
        exit /b 1
    )
    echo [OK] uv installed
)

REM Check Python
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%i in ('python --version 2^>^&1') do set "PY_VER=%%i"
    echo [INFO] Python: !PY_VER!
) else (
    echo [ERROR] Python not found. Please install Python 3.11+
    exit /b 1
)

echo.

REM Create virtual environment
if "%SKIP_VENV%"=="false" (
    if exist ".venv" (
        echo [INFO] Virtual environment already exists
    ) else (
        echo [STEP] Creating virtual environment (Python 3.11^)...
        uv venv .venv --python 3.11
        if %errorlevel% neq 0 (
            echo [ERROR] Failed to create virtual environment
            exit /b 1
        )
        echo [OK] Virtual environment created
    )

    REM Activate virtual environment
    call .venv\Scripts\activate.bat
)

REM Install base
echo [STEP] Installing base dependencies...
uv pip install -r requirements\base.txt
echo [OK] Base dependencies installed

REM Install STT
if "%INSTALL_ALL%"=="true" goto :install_stt
if "%INSTALL_STT%"=="true" goto :install_stt
goto :skip_stt

:install_stt
echo [STEP] Installing STT dependencies for %PLATFORM_NAME%...

if "%PLATFORM%"=="cuda" (
    echo [INFO] Installing PyTorch with CUDA support...
    uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
    uv pip install -r requirements\stt-cuda.txt
) else (
    uv pip install -r requirements\stt-cpu.txt
)
echo [OK] STT dependencies installed
:skip_stt

REM Install LLM
if "%INSTALL_ALL%"=="true" goto :install_llm
if "%INSTALL_LLM%"=="true" goto :install_llm
goto :skip_llm

:install_llm
echo [STEP] Installing LLM dependencies for %PLATFORM_NAME%...

if "%PLATFORM%"=="cuda" (
    uv pip install -r requirements\llm-cuda.txt
) else (
    echo [WARN] LLM is not recommended on CPU (no GPU acceleration^)
    echo [INFO] Skipping LLM installation
)
echo [OK] LLM dependencies installed
:skip_llm

REM Install dev
if "%INSTALL_DEV%"=="true" (
    echo [STEP] Installing development dependencies...
    uv pip install pytest pytest-asyncio ruff
    echo [OK] Dev dependencies installed
)

echo.
echo ============================================================
echo [OK] Installation complete!
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
