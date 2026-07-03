#!/usr/bin/env bash
# Voice Input Framework - uv Installation Script
# Fast, lightweight setup using uv package manager
#
# Usage:
#   ./install-uv.sh              # Auto-detect and install
#   ./install-uv.sh --stt-only   # STT only
#   ./install-uv.sh --llm-only   # LLM only
#   ./install-uv.sh --all        # Everything
#   ./install-uv.sh --dev        # Include dev dependencies

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${CYAN}[STEP]${NC} $1"; }

# Detect platform
detect_platform() {
    local system=$(uname -s 2>/dev/null || echo "Windows")
    local arch=$(uname -m 2>/dev/null || echo "x86_64")
    
    case "$system" in
        Darwin)
            if [[ "$arch" == "arm64" ]]; then
                PLATFORM="apple_silicon"
                PLATFORM_NAME="Apple Silicon (macOS)"
                PYTHON_VERSION="3.11"
            else
                PLATFORM="cpu"
                PLATFORM_NAME="Intel Mac (macOS)"
                PYTHON_VERSION="3.11"
            fi
            ;;
        Linux)
            if command -v nvidia-smi &> /dev/null; then
                PLATFORM="cuda"
                GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
                GPU_MEMORY=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
                PLATFORM_NAME="NVIDIA GPU (Linux): $GPU_NAME"
                PYTHON_VERSION="3.11"
            else
                PLATFORM="cpu"
                PLATFORM_NAME="CPU (Linux)"
                PYTHON_VERSION="3.11"
            fi
            ;;
        MINGW*|MSYS*|CYGWIN*|Windows*)
            if command -v nvidia-smi &> /dev/null; then
                PLATFORM="cuda"
                GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
                PLATFORM_NAME="NVIDIA GPU (Windows): $GPU_NAME"
            else
                PLATFORM="cpu"
                PLATFORM_NAME="CPU (Windows)"
            fi
            PYTHON_VERSION="3.11"
            ;;
        *)
            PLATFORM="cpu"
            PLATFORM_NAME="Unknown ($system)"
            PYTHON_VERSION="3.11"
            ;;
    esac
}

# Check if uv is installed
check_uv() {
    if command -v uv &> /dev/null; then
        UV_VERSION=$(uv --version)
        print_info "uv found: $UV_VERSION"
        return 0
    else
        return 1
    fi
}

# Install uv
install_uv() {
    print_step "Installing uv..."
    
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
        # Windows
        powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
    else
        # macOS/Linux
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    
    # Add to PATH for current session
    export PATH="$HOME/.cargo/bin:$PATH"
    
    if command -v uv &> /dev/null; then
        print_success "uv installed: $(uv --version)"
    else
        print_error "Failed to install uv"
        exit 1
    fi
}

# Create virtual environment
create_venv() {
    local venv_dir="${1:-.venv}"
    
    if [ -d "$venv_dir" ]; then
        print_info "Virtual environment already exists: $venv_dir"
        return 0
    fi
    
    print_step "Creating virtual environment (Python $PYTHON_VERSION)..."
    uv venv "$venv_dir" --python "$PYTHON_VERSION"
    print_success "Virtual environment created: $venv_dir"
}

# Install base dependencies
install_base() {
    print_step "Installing base dependencies..."
    uv pip install -r requirements/base.txt
    print_success "Base dependencies installed"
}

# Install STT dependencies
install_stt() {
    print_step "Installing STT dependencies for $PLATFORM_NAME..."
    
    case $PLATFORM in
        apple_silicon)
            uv pip install -r requirements/stt-mlx.txt
            print_success "STT MLX dependencies installed"
            ;;
        cuda)
            print_info "Installing PyTorch with CUDA support..."
            uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
            uv pip install -r requirements/stt-cuda.txt
            print_success "STT CUDA dependencies installed"
            ;;
        cpu)
            uv pip install -r requirements/stt-cpu.txt
            print_success "STT CPU dependencies installed"
            ;;
    esac
}

# Install LLM dependencies
install_llm() {
    print_step "Installing LLM dependencies for $PLATFORM_NAME..."
    
    case $PLATFORM in
        apple_silicon)
            uv pip install -r requirements/llm-mlx.txt
            print_success "LLM MLX dependencies installed"
            ;;
        cuda)
            # PyTorch should already be installed from STT
            if ! python3 -c "import torch" 2>/dev/null; then
                print_info "Installing PyTorch with CUDA support..."
                uv pip install torch --index-url https://download.pytorch.org/whl/cu124
            fi
            uv pip install -r requirements/llm-cuda.txt
            print_success "LLM CUDA dependencies installed"
            ;;
        cpu)
            print_warning "LLM is not recommended on CPU (no GPU acceleration)"
            print_info "Skipping LLM installation"
            ;;
    esac
}

# Install dev dependencies
install_dev() {
    print_step "Installing development dependencies..."
    uv pip install pytest pytest-asyncio ruff
    print_success "Dev dependencies installed"
}

# Print usage
usage() {
    echo "Voice Input Framework - uv Installation Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --all         Install all dependencies (STT + LLM)"
    echo "  --stt-only    Install STT dependencies only"
    echo "  --llm-only    Install LLM dependencies only"
    echo "  --dev         Include development dependencies"
    echo "  --no-venv     Skip virtual environment creation"
    echo "  --help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                # Auto-detect and install"
    echo "  $0 --all          # Install everything"
    echo "  $0 --stt-only     # STT only"
    echo "  $0 --dev          # Include dev tools"
}

# Main
main() {
    local install_stt_flag=false
    local install_llm_flag=false
    local install_all=false
    local install_dev_flag=false
    local skip_venv=false
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --all)
                install_all=true
                shift
                ;;
            --stt-only)
                install_stt_flag=true
                shift
                ;;
            --llm-only)
                install_llm_flag=true
                shift
                ;;
            --dev)
                install_dev_flag=true
                shift
                ;;
            --no-venv)
                skip_venv=true
                shift
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
    
    # If no flags, install all
    if [[ "$install_stt_flag" == false && "$install_llm_flag" == false && "$install_all" == false ]]; then
        install_all=true
    fi
    
    echo "============================================================"
    echo "Voice Input Framework - uv Installation"
    echo "============================================================"
    
    # Detect platform
    detect_platform
    print_info "Platform: $PLATFORM_NAME"
    
    # Check/install uv
    if ! check_uv; then
        install_uv
    fi
    
    # Check Python
    if command -v python3 &> /dev/null; then
        PYTHON_VER=$(python3 --version 2>&1)
        print_info "Python: $PYTHON_VER"
    elif command -v python &> /dev/null; then
        PYTHON_VER=$(python --version 2>&1)
        print_info "Python: $PYTHON_VER"
    else
        print_error "Python not found. Please install Python 3.11+"
        exit 1
    fi
    
    echo ""
    
    # Create virtual environment
    if [[ "$skip_venv" == false ]]; then
        create_venv
        # Activate virtual environment
        if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
            source .venv/Scripts/activate
        else
            source .venv/bin/activate
        fi
    fi
    
    # Install
    install_base
    
    if [[ "$install_all" == true || "$install_stt_flag" == true ]]; then
        install_stt
    fi
    
    if [[ "$install_all" == true || "$install_llm_flag" == true ]]; then
        install_llm
    fi
    
    if [[ "$install_dev_flag" == true ]]; then
        install_dev
    fi
    
    echo ""
    echo "============================================================"
    print_success "Installation complete!"
    echo "============================================================"
    echo ""
    echo "Next steps:"
    
    if [[ "$skip_venv" == false ]]; then
        echo "  1. Activate environment:"
        if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
            echo "     .venv\\Scripts\\activate"
        else
            echo "     source .venv/bin/activate"
        fi
    fi
    
    echo "  2. Start STT server:  python -m services.stt_server"
    echo "  3. Start LLM server:  python -m services.llm_server"
    echo "  4. Run client:        python run_client.py"
    echo ""
    echo "Environment size: $(du -sh .venv 2>/dev/null | cut -f1 || echo 'N/A')"
}

main "$@"
