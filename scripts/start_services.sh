#!/bin/bash
# Voice Input Framework - Services Launcher
# 启动 STT 和 LLM 分离服务

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 默认端口
STT_PORT=${VIF_STT_PORT:-6544}
LLM_PORT=${VIF_LLM_PORT:-6545}

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 conda 环境
check_conda_env() {
    local env_name=$1
    if ! conda env list | grep -q "^${env_name} "; then
        log_error "Conda environment '${env_name}' not found"
        return 1
    fi
    return 0
}

# 启动 STT 服务
start_stt_service() {
    log_info "Starting STT Service on port ${STT_PORT}..."
    
    # 检查 vif-stt 环境
    if ! check_conda_env "vif-stt"; then
        log_warn "Creating vif-stt environment..."
        create_stt_env
    fi
    
    # 在后台启动 STT 服务
    cd "$PROJECT_DIR"
    nohup conda run -n vif-stt python -m services.stt_server > /tmp/vif-stt.log 2>&1 &
    STT_PID=$!
    echo $STT_PID > /tmp/vif-stt.pid
    
    log_info "STT Service started (PID: $STT_PID)"
    log_info "Logs: /tmp/vif-stt.log"
}

# 启动 LLM 服务
start_llm_service() {
    log_info "Starting LLM Service on port ${LLM_PORT}..."
    
    # 检查 mlx-test 环境
    if ! check_conda_env "mlx-test"; then
        log_error "mlx-test environment not found. Please create it first."
        exit 1
    fi
    
    # 在后台启动 LLM 服务
    cd "$PROJECT_DIR"
    nohup conda run -n mlx-test python -m services.llm_server > /tmp/vif-llm.log 2>&1 &
    LLM_PID=$!
    echo $LLM_PID > /tmp/vif-llm.pid
    
    log_info "LLM Service started (PID: $LLM_PID)"
    log_info "Logs: /tmp/vif-llm.log"
}

# 创建 STT 环境
create_stt_env() {
    log_info "Creating vif-stt conda environment..."
    
    conda create -n vif-stt python=3.11 -y
    conda activate vif-stt
    
    # 安装 transformers 4.x
    pip install "transformers<5.0"
    
    # 安装 qwen_asr
    pip install qwen_asr
    
    # 安装 torch
    pip install torch
    
    log_info "vif-stt environment created successfully"
}

# 停止所有服务
stop_services() {
    log_info "Stopping services..."
    
    if [ -f /tmp/vif-stt.pid ]; then
        STT_PID=$(cat /tmp/vif-stt.pid)
        kill $STT_PID 2>/dev/null || true
        rm /tmp/vif-stt.pid
        log_info "STT Service stopped"
    fi
    
    if [ -f /tmp/vif-llm.pid ]; then
        LLM_PID=$(cat /tmp/vif-llm.pid)
        kill $LLM_PID 2>/dev/null || true
        rm /tmp/vif-llm.pid
        log_info "LLM Service stopped"
    fi
}

# 检查服务状态
check_status() {
    log_info "Checking service status..."
    
    # STT Service
    if [ -f /tmp/vif-stt.pid ]; then
        STT_PID=$(cat /tmp/vif-stt.pid)
        if ps -p $STT_PID > /dev/null 2>&1; then
            echo -e "STT Service: ${GREEN}running${NC} (PID: $STT_PID)"
            # 检查健康
            curl -s "http://localhost:${STT_PORT}/health" | python3 -m json.tool 2>/dev/null || echo "  Health check failed"
        else
            echo -e "STT Service: ${RED}stopped${NC} (stale PID file)"
        fi
    else
        echo -e "STT Service: ${YELLOW}not started${NC}"
    fi
    
    # LLM Service
    if [ -f /tmp/vif-llm.pid ]; then
        LLM_PID=$(cat /tmp/vif-llm.pid)
        if ps -p $LLM_PID > /dev/null 2>&1; then
            echo -e "LLM Service: ${GREEN}running${NC} (PID: $LLM_PID)"
            # 检查健康
            curl -s "http://localhost:${LLM_PORT}/health" | python3 -m json.tool 2>/dev/null || echo "  Health check failed"
        else
            echo -e "LLM Service: ${RED}stopped${NC} (stale PID file)"
        fi
    else
        echo -e "LLM Service: ${YELLOW}not started${NC}"
    fi
}

# 显示帮助
show_help() {
    echo "Voice Input Framework - Services Launcher"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  start       Start both STT and LLM services"
    echo "  stop        Stop all services"
    echo "  restart     Restart all services"
    echo "  status      Check service status"
    echo "  stt         Start only STT service"
    echo "  llm         Start only LLM service"
    echo "  help        Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  VIF_STT_PORT  STT service port (default: 6544)"
    echo "  VIF_LLM_PORT  LLM service port (default: 6545)"
}

# 主函数
main() {
    case "${1:-help}" in
        start)
            start_stt_service
            start_llm_service
            ;;
        stop)
            stop_services
            ;;
        restart)
            stop_services
            sleep 2
            start_stt_service
            start_llm_service
            ;;
        status)
            check_status
            ;;
        stt)
            start_stt_service
            ;;
        llm)
            start_llm_service
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
