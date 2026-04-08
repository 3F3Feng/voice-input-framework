@echo off
REM Voice Input Framework - GUI 客户端构建脚本 (Windows)
REM
REM 使用方法:
REM   1. 双击运行此脚本，或
REM   2. 在命令行中运行: build_gui.bat

echo === Voice Input Framework GUI 构建脚本 ===
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 Python
    echo 请从 https://python.org 安装 Python
    pause
    exit /b 1
)

REM 安装依赖
echo [1/3] 安装依赖...
pip install --upgrade pip
pip install websockets sounddevice pyperclip PySimpleGUI pyinstaller
if errorlevel 1 (
    echo 错误: 安装依赖失败
    pause
    exit /b 1
)

REM 清理旧构建
echo [2/3] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__
if exist gui_client.spec del gui_client.spec

REM 创建 spec 文件
echo [3/3] 创建 PyInstaller 配置...

(
echo # -*- mode: python ; coding: utf-8 -*-
echo block_cipher = None
echo.
echo a = Analysis(
echo     ['gui_client.py'],
echo     pathex=[],
echo     binaries=[],
echo     datas=[],
echo     hiddenimports=[
echo         'websockets',
echo         'sounddevice',
echo         'pyperclip',
echo         'PySimpleGUI',
echo     ],
echo     hookspath=[],
echo     hooksconfig={},
echo     runtime_hooks=[],
echo     excludes=[],
echo     win_no_prefer_redirects=False,
echo     win_private_assemblies=False,
echo     cipher=block_cipher,
echo     noarchive=False,
echo )
echo.
echo pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
echo.
echo exe = EXE(
echo     pyz,
echo     a.scripts,
echo     [],
echo     exclude_binaries=True,
echo     name='VoiceInputGUI',
echo     debug=False,
echo     bootloader_ignore_signals=False,
echo     strip=False,
echo     upx=True,
echo     console=False,
echo     disable_windowed_traceback=False,
echo     argv_emulation=False,
echo     target_arch=None,
echo     codesign_identity=None,
echo     entitlements_file=None,
echo     icon=None,
echo )
echo.
echo coll = COLLECT(
echo     exe,
echo     a.binaries,
echo     a.zipfiles,
echo     a.datas,
echo     strip=False,
echo     upx=True,
echo     upx_exclude=[],
echo     name='VoiceInputGUI',
echo )
) > gui_client.spec

echo.
echo === 开始构建 ===
echo.
pyinstaller gui_client.spec --clean
if errorlevel 1 (
    echo.
    echo 错误: 构建失败
    pause
    exit /b 1
)

echo.
echo === 构建完成 ===
echo.
echo exe 文件位于: dist\VoiceInputGUI\VoiceInputGUI.exe
echo.
echo 直接运行 VoiceInputGUI.exe 即可启动程序
echo.
pause
