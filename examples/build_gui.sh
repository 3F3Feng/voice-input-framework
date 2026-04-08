#!/bin/bash
# Voice Input Framework - GUI 客户端打包脚本
#
# 在 Windows 上运行 (需要 PyInstaller):
#   pip install pyinstaller websockets sounddevice pyperclip PySimpleGUI
#   pyinstaller gui_client.spec

set -e

cd "$(dirname "$0")"

echo "=== Voice Input Framework GUI 构建脚本 ==="
echo ""

# 检查 Python
if ! command -v python &> /dev/null; then
    echo "错误: 未找到 Python"
    exit 1
fi

# 检查 pip
if ! python -m pip --version &> /dev/null; then
    echo "错误: 未找到 pip"
    exit 1
fi

# 安装依赖
echo "[1/3] 安装依赖..."
python -m pip install --upgrade pip
python -m pip install websockets sounddevice pyperclip PySimpleGUI pyinstaller

# 清理旧构建
echo "[2/3] 清理旧构建..."
rm -rf build dist __pycache__
rm -f voice_input_framework.spec

# 创建 spec 文件
echo "[3/3] 创建 PyInstaller 配置..."

cat > gui_client.spec << 'SPEC_EOF'
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['gui_client.py'],
    pathex=[],
    binaries=[],
    datas=[
        # 包含 PySimpleGUI 主题
    ],
    hiddenimports=[
        'websockets',
        'sounddevice',
        'pyperclip',
        'PySimpleGUI',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoiceInputGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VoiceInputGUI',
)
SPEC_EOF

echo "PyInstaller 配置已创建: gui_client.spec"
echo ""
echo "=== 构建说明 ==="
echo ""
echo "Windows 用户请运行以下命令:"
echo ""
echo "  pip install pyinstaller"
echo "  pyinstaller gui_client.spec"
echo ""
echo "构建完成后，exe 文件位于:"
echo "  dist/VoiceInputGUI/VoiceInputGUI.exe"
echo ""
echo "Linux/macOS 用户请安装 PyInstaller 后运行:"
echo "  pyinstaller gui_client.spec"
echo ""
