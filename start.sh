#!/usr/bin/env bash
# Voice Input Framework - Server Startup Script
# Start STT and/or LLM servers
#
# Usage:
#   ./start.sh              # Start STT server only
#   ./start.sh --all        # Start both STT and LLM
#   ./start.sh --llm        # Start LLM server only
#   ./start.sh --bg         # Start in background

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Default
START_STT=false
START_LLM=false
BACKGROUND=false
STT_PID_FILE=".stt.pid"
LLM_PID_FILE=".llm.pid"

usage() {
    echo "Voice Input Framework - Server Startup"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --all       Start both STT and LLM servers"
    echo "  --stt       Start STT server only (default)"
    echo "  --llm       Start LLM server only"
    echo "  --bg        Run in background"
    echo "  --stop      Stop background servers"
    echo "  --status    Check server status"
    echo "  --help      Show this help"
    echo ""
    echo "Examples:"
    echo "  $0              # Start STT in foreground"
    echo "  $0 --all --bg   # Start both in background"
    echo "  $0 --stop       # Stop all servers"
}

stop_servers() {
    print_info "Stopping servers..."
    
    if [ -f "$STT_PID_FILE" ]; then
        STT_PID=$(cat "$STT_PID_FILE")
        if kill -0 "$STT_PID" 2>/dev/null; then
            kill "$STT_PID"
            print_success "STT server stopped (PID: $STT_PID)"
        else
            print_warning "STT server not running (stale PID file)"
        fi
        rm -f "$STT_PID_FILE"
    else
        print_info "No STT server PID file found"
    fi
    
    if [ -f "$LLM_PID_FILE" ]; then
        LLM_PID=$(cat "$LLM_PID_FILE")
        if kill -0 "$LLM_PID" 2>/dev/null; then
            kill "$LLM_PID"
            print_success "LLM server stopped (PID: $LLM_PID)"
        else
            print_warning "LLM server not running (stale PID file)"
        fi
        rm -f "$LLM_PID_FILE"
    else
        print_info "No LLM server PID file found"
    fi
}

check_status() {
    echo "Server Status:"
    echo "--------------"
    
    if [ -f "$STT_PID_FILE" ]; then
        STT_PID=$(cat "$STT_PID_FILE")
        if kill -0 "$STT_PID" 2>/dev/null; then
            print_success "STT server running (PID: $STT_PID)"
        else
            print_warning "STT server not running (stale PID file)"
        fi
    else
        print_info "STT server not running"
    fi
    
    if [ -f "$LLM_PID_FILE" ]; then
        LLM_PID=$(cat "$LLM_PID_FILE")
        if kill -0 "$LLM_PID" 2>/dev/null; then
            print_success "LLM server running (PID: $LLM_PID)"
        else
            print_warning "LLM server not running (stale PID file)"
        fi
    else
        print_info "LLM server not running"
    fi
}

start_stt() {
    if [ -f "$STT_PID_FILE" ]; then
        STT_PID=$(cat "$STT_PID_FILE")
        if kill -0 "$STT_PID" 2>/dev/null; then
            print_warning "STT server already running (PID: $STT_PID)"
            return 0
        fi
        rm -f "$STT_PID_FILE"
    fi
    
    print_info "Starting STT server..."
    
    if [ "$BACKGROUND" = true ]; then
        python -m services.stt_server &
        STT_PID=$!
        echo "$STT_PID" > "$STT_PID_FILE"
        print_success "STT server started in background (PID: $STT_PID)"
    else
        print_info "STT server starting... (Ctrl+C to stop)"
        python -m services.stt_server
    fi
}

start_llm() {
    if [ -f "$LLM_PID_FILE" ]; then
        LLM_PID=$(cat "$LLM_PID_FILE")
        if kill -0 "$LLM_PID" 2>/dev/null; then
            print_warning "LLM server already running (PID: $LLM_PID)"
            return 0
        fi
        rm -f "$LLM_PID_FILE"
    fi
    
    print_info "Starting LLM server..."
    
    if [ "$BACKGROUND" = true ]; then
        python -m services.llm_server &
        LLM_PID=$!
        echo "$LLM_PID" > "$LLM_PID_FILE"
        print_success "LLM server started in background (PID: $LLM_PID)"
    else
        print_info "LLM server starting... (Ctrl+C to stop)"
        python -m services.llm_server
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            START_STT=true
            START_LLM=true
            shift
            ;;
        --stt)
            START_STT=true
            shift
            ;;
        --llm)
            START_LLM=true
            shift
            ;;
        --bg)
            BACKGROUND=true
            shift
            ;;
        --stop)
            stop_servers
            exit 0
            ;;
        --status)
            check_status
            exit 0
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Default to STT if nothing specified
if [ "$START_STT" = false ] && [ "$START_LLM" = false ]; then
    START_STT=true
fi

echo "============================================================"
echo "Voice Input Framework - Server"
echo "============================================================"

# Check if virtual environment exists
if [ -d ".venv" ]; then
    print_info "Activating virtual environment..."
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
        source .venv/Scripts/activate
    else
        source .venv/bin/activate
    fi
fi

# Start servers
if [ "$START_STT" = true ] && [ "$START_LLM" = true ]; then
    # Start both
    if [ "$BACKGROUND" = true ]; then
        start_llm
        sleep 2  # Give LLM time to start
        start_stt
        echo ""
        print_success "Both servers started in background"
        print_info "STT: http://localhost:6544"
        print_info "LLM: http://localhost:6545"
        print_info "Use '$0 --status' to check status"
        print_info "Use '$0 --stop' to stop servers"
    else
        # Foreground: start LLM in background, STT in foreground
        start_llm
        sleep 2
        start_stt
    fi
elif [ "$START_STT" = true ]; then
    start_stt
elif [ "$START_LLM" = true ]; then
    start_llm
fi
