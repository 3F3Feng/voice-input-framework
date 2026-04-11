#!/bin/bash
# Voice Input Framework - STT Environment Setup
# 创建 vif-stt conda 环境并安装依赖

set -e

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

# 激活环境并安装依赖
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate ${ENV_NAME}

# 安装 PyTorch (先安装，因为其他包依赖它)
echo "Installing PyTorch..."
pip install torch

# 安装 transformers 4.x (兼容 qwen_asr)
echo "Installing transformers 4.x..."
pip install "transformers<5.0"

# 安装 qwen_asr
echo "Installing qwen_asr..."
pip install qwen_asr

# 安装其他依赖
echo "Installing other dependencies..."
pip install numpy fastapi uvicorn websockets python-multipart

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
echo "    python -m services.stt_server"
echo ""
