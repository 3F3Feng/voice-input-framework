#!/bin/bash
# Voice Input Framework - Auto Installation Script
# Automatically detects platform and installs appropriate dependencies
#
# Usage:
#   ./install.sh              # Auto-detect and install
#   ./install.sh --stt-only   # Install STT only
#   ./install.sh --llm-only   # Install LLM only
#   ./install.sh --all        # Install everything

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored output
print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Detect platform
detect_platform() {
    local system=$(uname -s)
    local arch=$(uname -m)
    
    # Detect Apple Silicon
    if [[ "$system" == "Darwin" && "$arch" == "arm64" ]]; then
        PLATFORM="apple_silicon"
        PLATFORM_NAME="Apple Silicon (macOS)"
    # Detect NVIDIA GPU
    elif command -v nvidia-smi &> /dev/null; then
        PLATFORM="cuda"
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
        GPU_MEMORY=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
        PLATFORM_NAME="NVIDIA GPU: $GPU_NAME (${GPU_MEMORY}MB)"
    # Detect Windows with NVIDIA
    elif [[ "$system" == "MINGW"* || "$system" == "MSYS"* || "$system" == "CYGWIN"* ]]; then
        if command -v nvidia-smi &> /dev/null; then
            PLATFORM="cuda"
            GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
            PLATFORM_NAME="NVIDIA GPU (Windows): $GPU_NAME"
        else
            PLATFORM="cpu"
            PLATFORM_NAME="CPU (Windows)"
        fi
    else
        PLATFORM="cpu"
        PLATFORM_NAME="CPU ($system $arch)"
    fi
}

# Install base dependencies
install_base() {
    print_info "Installing base dependencies..."
    pip install -r requirements/base.txt
    print_success "Base dependencies installed"
}

# Install STT dependencies
install_stt() {
    print_info "Installing STT dependencies for $PLATFORM_NAME..."
    
    case $PLATFORM in
        apple_silicon)
            pip install -r requirements/stt-mlx.txt
            print_success "STT MLX dependencies installed"
            ;;
        cuda)
            print_info "Installing PyTorch with CUDA support..."
            pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
            pip install -r requirements/stt-cuda.txt
            print_success "STT CUDA dependencies installed"
            ;;
        cpu)
            pip install -r requirements/stt-cpu.txt
            print_success "STT CPU dependencies installed"
            ;;
    esac
}

# Install LLM dependencies
install_llm() {
    print_info "Installing LLM dependencies for $PLATFORM_NAME..."
    
    case $PLATFORM in
        apple_silicon)
            pip install -r requirements/llm-mlx.txt
            print_success "LLM MLX dependencies installed"
            ;;
        cuda)
            print_info "Installing PyTorch with CUDA support..."
            pip install torch --index-url https://download.pytorch.org/whl/cu124
            pip install -r requirements/llm-cuda.txt
            print_success "LLM CUDA dependencies installed"
            ;;
        cpu)
            print_warning "LLM is not recommended on CPU (no GPU acceleration)"
            print_info "Skipping LLM installation"
            ;;
    esac
}

# Print usage
usage() {
    echo "Voice Input Framework - Installation Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --all         Install all dependencies (STT + LLM)"
    echo "  --stt-only    Install STT dependencies only"
    echo "  --llm-only    Install LLM dependencies only"
    echo "  --help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                # Auto-detect and install"
    echo "  $0 --all          # Install everything"
    echo "  $0 --stt-only     # STT only"
}

# Main
main() {
    local install_stt_flag=false
    local install_llm_flag=false
    local install_all=false
    
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
    echo "Voice Input Framework - Installation"
    echo "============================================================"
    
    # Detect platform
    detect_platform
    print_info "Detected platform: $PLATFORM_NAME"
    
    # Check Python
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 not found"
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 --version)
    print_info "Python: $PYTHON_VERSION"
    
    # Check pip
    if ! command -v pip &> /dev/null; then
        print_error "pip not found"
        exit 1
    fi
    
    echo ""
    
    # Install
    install_base
    
    if [[ "$install_all" == true || "$install_stt_flag" == true ]]; then
        install_stt
    fi
    
    if [[ "$install_all" == true || "$install_llm_flag" == true ]]; then
        install_llm
    fi
    
    echo ""
    echo "============================================================"
    print_success "Installation complete!"
    echo "============================================================"
    echo ""
    echo "Next steps:"
    echo "  1. Start STT server:  python -m services.stt_server"
    echo "  2. Start LLM server:  python -m services.llm_server"
    echo "  3. Run client:        python run_client.py"
}

main "$@"
