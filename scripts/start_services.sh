#!/bin/bash
# Voice Input Framework - Services Launcher
# 启动 STT 和 LLM 分离服务
# 支持多种 Python 环境：conda, venv, 原生环境

set -e

# Source shell 配置
if [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    source "$HOME/.bashrc"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${VIF_CONFIG_FILE:-$HOME/.voice_input_config.json}"

# 默认端口
STT_PORT=${VIF_STT_PORT:-6544}
LLM_PORT=${VIF_LLM_PORT:-6545}

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_debug() { echo -e "${BLUE}[DEBUG]${NC} $1"; }

# ============================================
# 环境检测与配置
# ============================================

# 从配置文件读取环境配置
read_env_config() {
    local service=$1  # stt 或 llm
    local config_key="${service}_env"
    
    if [ -f "$CONFIG_FILE" ]; then
        # 使用 Python 解析 JSON（更可靠）
        python3 -c "
import json
import sys
try:
    with open('$CONFIG_FILE') as f:
        config = json.load(f)
    env_config = config.get('$config_key', {})
    env_type = env_config.get('type', 'auto')
    env_path = env_config.get('path', '')
    env_name = env_config.get('name', '')
    python_bin = env_config.get('python_bin', '')
    
    # 输出格式: type|path|name|python_bin
    print(f'{env_type}|{env_path}|{env_name}|{python_bin}')
except Exception as e:
    print('auto|||')
" 2>/dev/null || echo "auto|||"
    else
        echo "auto|||"
    fi
}

# 检测 Python 环境
detect_python_env() {
    local service=$1
    local default_conda_env=$2
    
    # 读取配置
    local config_str=$(read_env_config "$service")
    local env_type=$(echo "$config_str" | cut -d'|' -f1)
    local env_path=$(echo "$config_str" | cut -d'|' -f2)
    local env_name=$(echo "$config_str" | cut -d'|' -f3)
    local python_bin=$(echo "$config_str" | cut -d'|' -f4)
    
    log_debug "Config for $service: type=$env_type, path=$env_path, name=$env_name, python=$python_bin"
    
    # 根据配置类型确定环境
    case "$env_type" in
        conda)
            # 明确指定 conda 环境
            if [ -n "$env_name" ]; then
                echo "conda|$env_name|conda run -n $env_name"
            else
                log_error "Conda environment type specified but no name given"
                return 1
            fi
            ;;
        venv)
            # Python venv 环境
            if [ -n "$env_path" ]; then
                local activate_script="$env_path/bin/activate"
                if [ -f "$activate_script" ]; then
                    echo "venv|$env_path|source $activate_script &&"
                else
                    log_error "Venv not found at: $env_path"
                    return 1
                fi
            else
                log_error "Venv environment type specified but no path given"
                return 1
            fi
            ;;
        native)
            # 原生 Python 环境
            if [ -n "$python_bin" ]; then
                echo "native|$python_bin|$python_bin"
            else
                # 使用系统 Python
                echo "native|python3|python3"
            fi
            ;;
        auto|*)
            # 自动检测
            detect_auto_env "$service" "$default_conda_env"
            ;;
    esac
}

# 自动检测环境
detect_auto_env() {
    local service=$1
    local default_conda_env=$2
    
    # 优先检测 conda 环境
    if command -v conda &>/dev/null; then
        if conda env list 2>/dev/null | grep -q "^${default_conda_env} "; then
            log_debug "Auto-detected conda env: $default_conda_env"
            echo "conda|$default_conda_env|conda run -n $default_conda_env"
            return 0
        fi
    fi
    
    # 检测项目目录下的 venv
    local venv_path="$PROJECT_DIR/.venv"
    if [ -f "$venv_path/bin/activate" ]; then
        log_debug "Auto-detected venv at: $venv_path"
        echo "venv|$venv_path|source $venv_path/bin/activate &&"
        return 0
    fi
    
    # 检测用户目录下的 venv
    venv_path="$HOME/.venv"
    if [ -f "$venv_path/bin/activate" ]; then
        log_debug "Auto-detected venv at: $venv_path"
        echo "venv|$venv_path|source $venv_path/bin/activate &&"
        return 0
    fi
    
    # 使用系统 Python
    log_debug "Using system Python"
    echo "native|python3|python3"
}

# ============================================
# 服务管理
# ============================================

# 启动 STT 服务
start_stt_service() {
    log_info "Starting STT Service on port ${STT_PORT}..."
    
    # 检测环境
    local env_info=$(detect_python_env "stt" "vif-stt")
    local env_type=$(echo "$env_info" | cut -d'|' -f1)
    local env_loc=$(echo "$env_info" | cut -d'|' -f2)
    local run_cmd=$(echo "$env_info" | cut -d'|' -f3-)
    
    log_info "Using $env_type environment: $env_loc"
    
    # 检查端口是否被占用
    if lsof -i :$STT_PORT >/dev/null 2>&1; then
        log_error "Port $STT_PORT is already in use"
        lsof -i :$STT_PORT
        return 1
    fi
    
    # 在后台启动 STT 服务
    cd "$PROJECT_DIR"
    
    local python_cmd
    case "$env_type" in
        conda)
            PYTHONUNBUFFERED=1 nohup $run_cmd python -m services.stt_server > /tmp/vif-stt.log 2>&1 &
            ;;
        venv)
            PYTHONUNBUFFERED=1 nohup bash -c "$run_cmd python -m services.stt_server" > /tmp/vif-stt.log 2>&1 &
            ;;
        native)
            PYTHONUNBUFFERED=1 nohup $run_cmd -m services.stt_server > /tmp/vif-stt.log 2>&1 &
            ;;
    esac
    
    local pid=$!
    echo $pid > /tmp/vif-stt.pid
    log_info "STT Service started (PID: $pid, Env: $env_type)"
    log_info "Logs: /tmp/vif-stt.log"
    
    # 等待服务启动
    sleep 2
    if check_service_health "STT" $STT_PORT; then
        log_info "STT Service is healthy"
    else
        log_warn "STT Service may not have started correctly. Check logs: /tmp/vif-stt.log"
    fi
}

# 启动 LLM 服务
start_llm_service() {
    log_info "Starting LLM Service on port ${LLM_PORT}..."
    
    # 检测环境
    local env_info=$(detect_python_env "llm" "mlx-test")
    local env_type=$(echo "$env_info" | cut -d'|' -f1)
    local env_loc=$(echo "$env_info" | cut -d'|' -f2)
    local run_cmd=$(echo "$env_info" | cut -d'|' -f3-)
    
    log_info "Using $env_type environment: $env_loc"
    
    # 检查端口是否被占用
    if lsof -i :$LLM_PORT >/dev/null 2>&1; then
        log_error "Port $LLM_PORT is already in use"
        lsof -i :$LLM_PORT
        return 1
    fi
    
    # 在后台启动 LLM 服务
    cd "$PROJECT_DIR"
    
    case "$env_type" in
        conda)
            PYTHONUNBUFFERED=1 nohup $run_cmd python -m services.llm_server > /tmp/vif-llm.log 2>&1 &
            ;;
        venv)
            PYTHONUNBUFFERED=1 nohup bash -c "$run_cmd python -m services.llm_server" > /tmp/vif-llm.log 2>&1 &
            ;;
        native)
            PYTHONUNBUFFERED=1 nohup $run_cmd -m services.llm_server > /tmp/vif-llm.log 2>&1 &
            ;;
    esac
    
    local pid=$!
    echo $pid > /tmp/vif-llm.pid
    log_info "LLM Service started (PID: $pid, Env: $env_type)"
    log_info "Logs: /tmp/vif-llm.log"
    
    # 等待服务启动
    sleep 2
    if check_service_health "LLM" $LLM_PORT; then
        log_info "LLM Service is healthy"
    else
        log_warn "LLM Service may not have started correctly. Check logs: /tmp/vif-llm.log"
    fi
}

# 检查服务健康
check_service_health() {
    local name=$1
    local port=$2
    local max_retries=3
    local retry=0
    
    while [ $retry -lt $max_retries ]; do
        if curl -s --connect-timeout 2 "http://localhost:${port}/health" >/dev/null 2>&1; then
            return 0
        fi
        retry=$((retry + 1))
        sleep 1
    done
    return 1
}

# 停止所有服务
stop_services() {
    log_info "Stopping services..."
    
    if [ -f /tmp/vif-stt.pid ]; then
        local pid=$(cat /tmp/vif-stt.pid)
        if kill $pid 2>/dev/null; then
            log_info "STT Service stopped (PID: $pid)"
        fi
        rm -f /tmp/vif-stt.pid
    fi
    
    if [ -f /tmp/vif-llm.pid ]; then
        local pid=$(cat /tmp/vif-llm.pid)
        if kill $pid 2>/dev/null; then
            log_info "LLM Service stopped (PID: $pid)"
        fi
        rm -f /tmp/vif-llm.pid
    fi
}

# 检查服务状态
check_status() {
    log_info "Checking service status..."
    
    # STT Service
    if [ -f /tmp/vif-stt.pid ]; then
        local pid=$(cat /tmp/vif-stt.pid)
        if ps -p $pid > /dev/null 2>&1; then
            echo -e "STT Service: ${GREEN}running${NC} (PID: $pid, Port: $STT_PORT)"
            local health=$(curl -s "http://localhost:${STT_PORT}/health" 2>/dev/null)
            if [ -n "$health" ]; then
                echo "  Health: $health"
            fi
        else
            echo -e "STT Service: ${RED}stopped${NC} (stale PID file)"
            rm -f /tmp/vif-stt.pid
        fi
    else
        echo -e "STT Service: ${YELLOW}not started${NC}"
    fi
    
    # LLM Service
    if [ -f /tmp/vif-llm.pid ]; then
        local pid=$(cat /tmp/vif-llm.pid)
        if ps -p $pid > /dev/null 2>&1; then
            echo -e "LLM Service: ${GREEN}running${NC} (PID: $pid, Port: $LLM_PORT)"
            local health=$(curl -s "http://localhost:${LLM_PORT}/health" 2>/dev/null)
            if [ -n "$health" ]; then
                echo "  Health: $health"
            fi
        else
            echo -e "LLM Service: ${RED}stopped${NC} (stale PID file)"
            rm -f /tmp/vif-llm.pid
        fi
    else
        echo -e "LLM Service: ${YELLOW}not started${NC}"
    fi
}

# ============================================
# 配置管理
# ============================================

# 生成配置模板
generate_config_template() {
    cat << 'EOF'
{
  "stt_env": {
    "type": "auto",
    "path": "",
    "name": "vif-stt",
    "python_bin": ""
  },
  "llm_env": {
    "type": "auto",
    "path": "",
    "name": "mlx-test",
    "python_bin": ""
  }
}
EOF
}

# 显示配置帮助
show_config_help() {
    echo "Environment Configuration"
    echo ""
    echo "Configuration file: $CONFIG_FILE"
    echo ""
    echo "Configuration format in ~/.voice_input_config.json:"
    echo ""
    generate_config_template
    echo ""
    echo "Environment Types:"
    echo "  auto   - Auto-detect (tries conda, then venv, then native)"
    echo "  conda  - Use conda environment (requires 'name' field)"
    echo "  venv   - Use Python venv (requires 'path' field)"
    echo "  native - Use system Python (optional 'python_bin' field)"
    echo ""
    echo "Examples:"
    echo ""
    echo "1. Conda environment:"
    echo '   {"stt_env": {"type": "conda", "name": "vif-stt"}}'
    echo ""
    echo "2. Venv environment:"
    echo '   {"stt_env": {"type": "venv", "path": "/path/to/venv"}}'
    echo ""
    echo "3. Native Python:"
    echo '   {"stt_env": {"type": "native", "python_bin": "/usr/bin/python3"}}'
    echo ""
    echo "4. Auto-detect (default):"
    echo '   {"stt_env": {"type": "auto", "name": "vif-stt"}}'
}

# 显示帮助
show_help() {
    echo "Voice Input Framework - Services Launcher"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  start     Start both STT and LLM services"
    echo "  stop      Stop all services"
    echo "  restart   Restart all services"
    echo "  status    Check service status"
    echo "  stt       Start only STT service"
    echo "  llm       Start only LLM service"
    echo "  config    Show configuration help"
    echo "  help      Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  VIF_STT_PORT    STT service port (default: 6544)"
    echo "  VIF_LLM_PORT    LLM service port (default: 6545)"
    echo "  VIF_CONFIG_FILE Configuration file path (default: ~/.voice_input_config.json)"
    echo ""
    echo "Supported Environment Types:"
    echo "  - conda  (Anaconda/Miniconda environments)"
    echo "  - venv   (Python virtual environments)"
    echo "  - native (System Python)"
}

# ============================================
# 主程序
# ============================================

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
        config)
            show_config_help
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
