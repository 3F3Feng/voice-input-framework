#!/bin/bash
# Voice Input Framework - STT Environment Setup
# 创建 vif-stt conda 环境并安装依赖

set -e

# Source conda environment
if [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    source "$HOME/.bashrc"
fi

echo "========================================"
echo "Voice Input Framework - STT Environment Setup"
echo "========================================"

# 检查 conda
if ! command -v conda &> /dev/null; then
    echo "Error: conda not found. Please install Anaconda or Miniconda first."
    exit 1
fi

ENV_NAME="vif-stt"

# 检查环境是否已存在
if conda env list | grep -q "^${ENV_NAME} "; then
    echo "Environment '${ENV_NAME}' already exists."
    read -p "Do you want to recreate it? (y/N): " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        echo "Removing existing environment..."
        conda env remove -n ${ENV_NAME} -y
    else
        echo "Aborted."
        exit 0
    fi
fi

echo ""
echo "Creating conda environment: ${ENV_NAME}"
echo "Python version: 3.11"
echo ""

# 创建环境
conda create -n ${ENV_NAME} python=3.11 -y

echo ""
echo "Installing dependencies..."
echo ""

# 使用 conda run 安装依赖
conda run -n ${ENV_NAME} pip install \
    torch \
    "transformers<5.0" \
    qwen_asr \
    numpy fastapi uvicorn websockets python-multipart

echo ""
echo "========================================"
echo "Environment setup complete!"
echo "========================================"
echo ""
echo "To activate the environment, run:"
echo "    conda activate ${ENV_NAME}"
echo ""
echo "To start the STT service, run:"
echo "    cd /Users/shifengzhang/voice-input-framework"
echo "    conda activate ${ENV_NAME}"
echo "    python -m services.stt_server"
echo ""
