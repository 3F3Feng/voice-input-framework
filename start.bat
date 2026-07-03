@echo off
REM Voice Input Framework - Server Startup Script (Windows)
REM Start STT and/or LLM servers
REM
REM Supports uv-style venvs: .venv-stt (STT), .venv-llm (LLM)
REM Falls back to .venv for backward compatibility
REM
REM Usage:
REM   start.bat              REM Start STT server only
REM   start.bat --all        REM Start both STT and LLM
REM   start.bat --llm        REM Start LLM server only
REM   start.bat --bg         REM Run in background
REM   start.bat --stop       REM Stop background servers
REM   start.bat --status     REM Check server status
REM   start.bat --help       REM Show this help

setlocal enabledelayedexpansion

set "START_STT=false"
set "START_LLM=false"
set "BACKGROUND=false"

REM Parse arguments
:parse_args
if "%~1"=="" goto :check_args
if "%~1"=="--all" (
    set "START_STT=true"
    set "START_LLM=true"
    shift
    goto :parse_args
)
if "%~1"=="--stt" (
    set "START_STT=true"
    shift
    goto :parse_args
)
if "%~1"=="--llm" (
    set "START_LLM=true"
    shift
    goto :parse_args
)
if "%~1"=="--bg" (
    set "BACKGROUND=true"
    shift
    goto :parse_args
)
if "%~1"=="--stop" (
    goto :stop_servers
)
if "%~1"=="--status" (
    goto :check_status
)
if "%~1"=="--help" (
    goto :usage
)
echo [ERROR] Unknown option: %~1
goto :usage

:check_args
if "%START_STT%"=="false" if "%START_LLM%"=="false" (
    set "START_STT=true"
)

echo ============================================================
echo Voice Input Framework - Server (Windows)
echo ============================================================

REM Resolve python path for STT (prefer .venv-stt over .venv)
set "STT_PYTHON=python"
if exist ".venv-stt\Scripts\python.exe" (
    set "STT_PYTHON=.venv-stt\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "STT_PYTHON=.venv\Scripts\python.exe"
)

REM Resolve python path for LLM (prefer .venv-llm over .venv)
set "LLM_PYTHON=python"
if exist ".venv-llm\Scripts\python.exe" (
    set "LLM_PYTHON=.venv-llm\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "LLM_PYTHON=.venv\Scripts\python.exe"
)

REM Start servers
if "%START_STT%"=="true" if "%START_LLM%"=="true" (
    goto :start_both
)
if "%START_STT%"=="true" (
    goto :start_stt
)
if "%START_LLM%"=="true" (
    goto :start_llm
)

:start_both
echo [INFO] Starting both servers...
echo [INFO] STT: !STT_PYTHON!
echo [INFO] LLM: !LLM_PYTHON!

if "%BACKGROUND%"=="true" (
    start "LLM Server" /min "!LLM_PYTHON!" -m services.llm_server
    timeout /t 2 /nobreak >nul
    start "STT Server" /min "!STT_PYTHON!" -m services.stt_server
    echo.
    echo [OK] Both servers started in background
    echo [INFO] STT: http://localhost:6544
    echo [INFO] LLM: http://localhost:6545
    echo [INFO] Use 'start.bat --status' to check status
    echo [INFO] Use 'start.bat --stop' to stop servers
) else (
    REM Start LLM in background, STT in foreground
    start "LLM Server" "!LLM_PYTHON!" -m services.llm_server
    timeout /t 2 /nobreak >nul
    echo [INFO] Starting STT server...
    "!STT_PYTHON!" -m services.stt_server
)
goto :end

:start_stt
echo [INFO] Starting STT server using: !STT_PYTHON!
if "%BACKGROUND%"=="true" (
    start "STT Server" /min "!STT_PYTHON!" -m services.stt_server
    echo [OK] STT server started in background
    echo [INFO] STT: http://localhost:6544
) else (
    "!STT_PYTHON!" -m services.stt_server
)
goto :end

:start_llm
echo [INFO] Starting LLM server using: !LLM_PYTHON!
if "%BACKGROUND%"=="true" (
    start "LLM Server" /min "!LLM_PYTHON!" -m services.llm_server
    echo [OK] LLM server started in background
    echo [INFO] LLM: http://localhost:6545
) else (
    "!LLM_PYTHON!" -m services.llm_server
)
goto :end

:stop_servers
echo [INFO] Stopping servers...
taskkill /FI "WindowTitle eq STT Server*" /F >nul 2>&1
taskkill /FI "WindowTitle eq LLM Server*" /F >nul 2>&1
echo [OK] Servers stopped
goto :end

:check_status
echo Server Status:
echo --------------
tasklist /FI "WindowTitle eq STT Server*" 2>nul | findstr /I "python" >nul
if %errorlevel%==0 (
    echo [OK] STT server running
) else (
    echo [INFO] STT server not running
)
tasklist /FI "WindowTitle eq LLM Server*" 2>nul | findstr /I "python" >nul
if %errorlevel%==0 (
    echo [OK] LLM server running
) else (
    echo [INFO] LLM server not running
)
goto :end

:usage
echo Voice Input Framework - Server Startup (Windows)
echo.
echo Usage: %~nx0 [OPTIONS]
echo.
echo Options:
echo   --all       Start both STT and LLM servers
echo   --stt       Start STT server only (default)
echo   --llm       Start LLM server only
echo   --bg        Run in background
echo   --stop      Stop background servers
echo   --status    Check server status
echo   --help      Show this help
echo.
echo Examples:
echo   %~nx0              REM Start STT in foreground
echo   %~nx0 --all --bg   REM Start both in background (uv venv)
echo   %~nx0 --stop       REM Stop all servers

:end
endlocal
