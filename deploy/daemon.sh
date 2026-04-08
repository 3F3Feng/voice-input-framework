#!/bin/bash
#
# Voice Input Framework - Daemon Management Script
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$HOME/.openclaw/workspace/.venv/bin/python3"
PID_FILE="$PROJECT_DIR/voice_input_framework.pid"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/voice_input_framework.log"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

start() {
    echo "Starting Voice Input Framework..."
    
    # 检查是否已运行
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Already running (PID: $PID)"
            return 1
        fi
        rm -f "$PID_FILE"
    fi
    
    # 启动服务
    cd "$PROJECT_DIR"
    nohup "$VENV_PYTHON" -m voice_input_framework.server.api \
        >> "$LOG_FILE" 2>&1 &
    
    PID=$!
    echo $PID > "$PID_FILE"
    echo "Started (PID: $PID)"
    echo "Log: $LOG_FILE"
}

stop() {
    echo "Stopping Voice Input Framework..."
    
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            sleep 2
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID"
            fi
            echo "Stopped"
        else
            echo "Not running"
        fi
        rm -f "$PID_FILE"
    else
        echo "Not running (no PID file)"
    fi
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Running (PID: $PID)"
            
            # 检查端口
            if command -v lsof &> /dev/null; then
                PORT=$(lsof -p "$PID" -iTCP -sTCP:LISTEN -n -P 2>/dev/null | grep LISTEN | awk '{print $9}' | cut -d':' -f2 || echo "unknown")
                echo "Port: $PORT"
            fi
            return 0
        fi
    fi
    echo "Not running"
    return 1
}

restart() {
    stop
    sleep 1
    start
}

tail-log() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "Log file not found: $LOG_FILE"
    fi
}

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
    *)
        echo "Usage: $0 {start|stop|restart|status|log}"
        exit 1
        ;;
esac
