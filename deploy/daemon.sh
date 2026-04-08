#!/bin/bash
#
# Voice Input Framework - Daemon Management Script
# 支持首次启动自动下载模型
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$HOME/.openclaw/workspace/.venv/bin/python3"
PID_FILE="$PROJECT_DIR/voice_input_framework.pid"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/voice_input_framework.log"
MODELS_DIR="$PROJECT_DIR/models"
PYTHON_CMD="$VENV_PYTHON"

# 默认配置
DEFAULT_MODEL="${VIF_DEFAULT_MODEL:-qwen_asr}"
PORT="${VIF_PORT:-6543}"
HOST="${VIF_HOST:-0.0.0.0}"

# 模型配置
declare -A MODEL_SIZES
MODEL_SIZES["whisper-small"]="~242MB"
MODEL_SIZES["qwen_asr"]="~1.3GB"
MODEL_SIZES["whisper-large-v3"]="~3.1GB"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# 检查模型是否已下载
check_model() {
    local model_name="$1"
    local model_path="$MODELS_DIR/$model_name"
    
    # Whisper 模型检查 config.json
    if [ -d "$model_path" ] && [ -f "$model_path/config.json" ]; then
        return 0
    fi
    
    # Qwen 模型检查配置
    if [ -d "$model_path" ] && [ -f "$model_path" ]; then
        return 0
    fi
    
    return 1
}

# 下载模型
download_model() {
    local model_name="$1"
    local model_path="$MODELS_DIR/$model_name"
    local size="${MODEL_SIZES[$model_name]:-'unknown'}"
    
    if check_model "$model_name"; then
        log "[模型] $model_name 已存在，跳过下载"
        return 0
    fi
    
    log "[模型] 开始下载 $model_name ($size)..."
    
    case "$model_name" in
        "whisper-small")
            mkdir -p "$model_path"
            cd "$model_path"
            git lfs install --force 2>/dev/null || true
            if command -v huggingface-cli &> /dev/null; then
                huggingface-cli download openai/whisper-small --local . 2>&1 | tee -a "$LOG_FILE"
            else
                git clone https://huggingface.co/openai/whisper-small . 2>&1 | tee -a "$LOG_FILE"
            fi
            ;;
        "qwen_asr")
            mkdir -p "$model_path"
            cd "$model_path"
            # Qwen3-ASR 从 ModelScope 下载（国内快）
            if command -v modelscope &> /dev/null; then
                modelscope download Qwen/Qwen3-ASR-0.6B --local . 2>&1 | tee -a "$LOG_FILE"
            else
                # 备选 HuggingFace
                git lfs install --force 2>/dev/null || true
                git clone https://huggingface.co/Qwen/Qwen3-ASR-0.6B . 2>&1 | tee -a "$LOG_FILE"
            fi
            ;;
        "whisper-large-v3")
            mkdir -p "$model_path"
            cd "$model_path"
            if command -v huggingface-cli &> /dev/null; then
                huggingface-cli download openai/whisper-large-v3 --local . 2>&1 | tee -a "$LOG_FILE"
            else
                git lfs install --force 2>/dev/null || true
                git clone https://huggingface.co/openai/whisper-large-v3 . 2>&1 | tee -a "$LOG_FILE"
            fi
            ;;
        *)
            log "[错误] 未知模型: $model_name"
            return 1
            ;;
    esac
    
    if check_model "$model_name"; then
        log "[模型] $model_name 下载完成"
        return 0
    else
        log "[错误] $model_name 下载可能失败"
        return 1
    fi
}

# 下载默认模型
download_default_model() {
    log "[启动] 检查模型..."
    
    # 检查并下载默认模型
    if ! check_model "$DEFAULT_MODEL"; then
        log "[启动] 默认模型 $DEFAULT_MODEL 未找到，开始下载..."
        download_model "$DEFAULT_MODEL"
    else
        log "[启动] 模型 $DEFAULT_MODEL 已就绪"
    fi
}

# 检查服务是否运行
is_running() {
    if [ -f "$PID_FILE" ]; then
        local PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# 启动服务
start() {
    log "[启动] Voice Input Framework..."
    
    if is_running; then
        local PID=$(cat "$PID_FILE")
        log "[启动] 服务已在运行 (PID: $PID)"
        return 1
    fi
    
    # 首次启动下载模型
    if [ "${VIF_AUTO_DOWNLOAD:-true}" != "false" ]; then
        download_default_model
    fi
    
    # 启动服务
    cd "$PROJECT_DIR"
    
    # 设置环境变量
    export VIF_PORT="$PORT"
    export VIF_HOST="$HOST"
    export VIF_DEFAULT_MODEL="$DEFAULT_MODEL"
    export VIF_LOG_LEVEL="${VIF_LOG_LEVEL:-INFO}"
    export VIF_LOG_FILE="$LOG_FILE"
    
    log "[启动] 服务端口: $PORT, 模型: $DEFAULT_MODEL"
    
    nohup "$PYTHON_CMD" -m voice_input_framework.server.api \
        >> "$LOG_FILE" 2>&1 &
    
    local PID=$!
    echo $PID > "$PID_FILE"
    
    # 等待服务启动
    sleep 3
    
    if kill -0 "$PID" 2>/dev/null; then
        log "[启动] 服务已启动 (PID: $PID)"
        log "[启动] WebSocket: ws://$HOST:$PORT/ws/stream"
        log "[启动] HTTP: http://$HOST:$PORT"
        return 0
    else
        log "[错误] 服务启动失败，查看日志: tail -f $LOG_FILE"
        return 1
    fi
}

# 停止服务
stop() {
    log "[停止] Voice Input Framework..."
    
    if [ -f "$PID_FILE" ]; then
        local PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            sleep 2
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID"
            fi
            log "[停止] 服务已停止"
        else
            log "[停止] 服务未运行"
        fi
        rm -f "$PID_FILE"
    else
        log "[停止] 服务未运行 (无 PID 文件)"
    fi
}

# 查看状态
status() {
    if is_running; then
        local PID=$(cat "$PID_FILE")
        log "[状态] 运行中 (PID: $PID)"
        
        # 检查端口
        if command -v lsof &> /dev/null; then
            local PORT_INFO=$(lsof -iTCP:$PORT -sTCP:LISTEN -n -P 2>/dev/null | grep LISTEN || echo "端口检测失败")
            log "[状态] $PORT_INFO"
        fi
        
        # 健康检查
        if command -v curl &> /dev/null; then
            local HEALTH=$(curl -s --connect-timeout 3 "http://localhost:$PORT/health" 2>/dev/null | head -c 200 || echo "健康检查失败")
            log "[状态] 健康检查: $HEALTH"
        fi
        
        return 0
    fi
    log "[状态] 未运行"
    return 1
}

# 重启服务
restart() {
    stop
    sleep 2
    start
}

# 查看日志
tail-log() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "日志文件不存在: $LOG_FILE"
    fi
}

# 下载模型（手动触发）
download() {
    if [ -z "$1" ]; then
        log "[下载] 用法: $0 download <model-name>"
        log "[下载] 可用模型: whisper-small, qwen_asr, whisper-large-v3"
        return 1
    fi
    
    download_model "$1"
}

# 显示帮助
show_help() {
    echo "Voice Input Framework - 服务管理脚本"
    echo
    echo "用法: $0 <命令> [参数]"
    echo
    echo "命令:"
    echo "  start              启动服务（首次自动下载模型）"
    echo "  stop               停止服务"
    echo "  restart            重启服务"
    echo "  status             查看服务状态"
    echo "  log                查看实时日志"
    echo "  download <model>   手动下载模型"
    echo "  help               显示帮助"
    echo
    echo "环境变量:"
    echo "  VIF_PORT           服务端口 (默认: 6543)"
    echo "  VIF_HOST           监听地址 (默认: 0.0.0.0)"
    echo "  VIF_DEFAULT_MODEL  默认模型 (默认: qwen_asr)"
    echo "  VIF_AUTO_DOWNLOAD  首次启动自动下载 (默认: true)"
    echo "  VIF_LOG_LEVEL      日志级别 (默认: INFO)"
    echo
    echo "示例:"
    echo "  $0 start                    # 启动服务"
    echo "  VIF_PORT=8080 $0 start      # 指定端口启动"
    echo "  $0 download whisper-small   # 下载指定模型"
}

# 主入口
case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    log)
        tail-log
        ;;
    download)
        download "$2"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        if [ -z "$1" ]; then
            show_help
        else
            echo "未知命令: $1"
            echo "运行 '$0 help' 查看帮助"
        fi
        ;;
esac
