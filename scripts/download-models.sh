#!/bin/bash
#
# Voice Input Framework - 模型下载脚本
# 自动下载预训练的 STT 模型文件
#

set -e

MODELS_DIR="${VOICE_INPUT_FRAMEWORK_MODELS_DIR:-$HOME/.voice-input-framework/models}"
mkdir -p "$MODELS_DIR"

echo "=== Voice Input Framework 模型下载 ==="
echo "模型目录: $MODELS_DIR"
echo

# Whisper Small (约 242MB)
download_whisper_small() {
    local model_path="$MODELS_DIR/whisper-small"
    if [ -d "$model_path" ]; then
        echo "[跳过] Whisper Small 已存在"
        return
    fi
    
    echo "[下载] Whisper Small..."
    mkdir -p "$model_path"
    
    # 从 HuggingFace 下载
    git lfs install --force 2>/dev/null || true
    cd "$model_path"
    
    # 使用 huggingface-cli 或直接 git clone
    if command -v huggingface-cli &> /dev/null; then
        huggingface-cli download openai/whisper-small --local .
    else
        git clone https://huggingface.co/openai/whisper-small .
    fi
    
    echo "[完成] Whisper Small"
}

# Qwen3-ASR 0.6B (约 1.3GB)
download_qwen_asr_06b() {
    local model_path="$MODELS_DIR/qwen3-asr-0.6b"
    if [ -d "$model_path" ]; then
        echo "[跳过] Qwen3-ASR 0.6B 已存在"
        return
    fi
    
    echo "[下载] Qwen3-ASR 0.6B (约 1.3GB)..."
    mkdir -p "$model_path"
    cd "$model_path"
    
    # 从 ModelScope 下载（国内更快）
    if command -v modelscope &> /dev/null; then
        modelscope download Qwen/Qwen3-ASR-0.6B --local .
    else
        # 备选：从 HuggingFace
        git clone https://huggingface.co/Qwen/Qwen3-ASR-0.6B .
    fi
    
    echo "[完成] Qwen3-ASR 0.6B"
}

# Whisper Large v3 (约 3.1GB)
download_whisper_large() {
    local model_path="$MODELS_DIR/whisper-large-v3"
    if [ -d "$model_path" ]; then
        echo "[跳过] Whisper Large v3 已存在"
        return
    fi
    
    echo "[下载] Whisper Large v3 (约 3.1GB)..."
    mkdir -p "$model_path"
    cd "$model_path"
    
    if command -v huggingface-cli &> /dev/null; then
        huggingface-cli download openai/whisper-large-v3 --local .
    else
        git clone https://huggingface.co/openai/whisper-large-v3 .
    fi
    
    echo "[完成] Whisper Large v3"
}

# 显示菜单
show_menu() {
    echo "可用模型:"
    echo "  1) Whisper Small (~242MB) - 轻量快速，英语为主"
    echo "  2) Qwen3-ASR 0.6B (~1.3GB) - 中英混输优化"
    echo "  3) Whisper Large v3 (~3.1GB) - 最高准确率"
    echo "  4) 下载所有"
    echo "  0) 退出"
    echo
}

# 主菜单
main() {
    if [ $# -gt 0 ]; then
        case "$1" in
            1) download_whisper_small ;;
            2) download_qwen_asr_06b ;;
            3) download_whisper_large ;;
            4) 
                download_whisper_small
                download_qwen_asr_06b
                download_whisper_large
                ;;
            *) echo "未知选项: $1" ;;
        esac
        return
    fi
    
    show_menu
    read -p "选择模型 [0-4]: " choice
    case "$choice" in
        1) download_whisper_small ;;
        2) download_qwen_asr_06b ;;
        3) download_whisper_large ;;
        4) 
            download_whisper_small
            download_qwen_asr_06b
            download_whisper_large
            ;;
        0) exit 0 ;;
        *) echo "无效选择" ;;
    esac
}

main "$@"
