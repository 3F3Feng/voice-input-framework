#!/bin/bash
# 运行 Voice Input Framework 单元测试

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Running Voice Input Framework tests..."
echo ""

# 运行 STT 服务测试（使用 vif-stt 环境）
echo "=== Testing STT Server ==="
conda run -n vif-stt -- python -m pytest \
    "$PROJECT_DIR/tests/test_stt_server.py" \
    -v --tb=short -m "not integration"

echo ""
echo "=== Testing LLM Server ==="
conda run -n mlx-test -- python -m pytest \
    "$PROJECT_DIR/tests/test_llm_server.py" \
    -v --tb=short -m "not integration"

echo ""
echo "=== Testing Models (any environment) ==="
# 使用任一环境测试基础模型
conda run -n vif-stt -- python -m pytest \
    "$PROJECT_DIR/tests/test_models.py" \
    -v --tb=short

echo ""
echo "Tests completed."
