#!/usr/bin/env bash
# 第 2 层等价性验证:真实模型集成测试
# 在有模型/GPU 的环境运行,验证修复前后真实推理全链路等价。
#
# 前置条件(任选其一环境):
#   - Apple Silicon + MLX(推荐):conda 环境 vif-stt / mlx-test
#   - 或 NVIDIA GPU + CUDA + transformers
#
# 用法:
#   bash scripts/run_integration.sh            # 默认端口 6544/6545
#   VIF_STT_PORT=6544 VIF_LLM_PORT=6545 bash scripts/run_integration.sh
set -euo pipefail

STT_PORT="${VIF_STT_PORT:-6544}"
LLM_PORT="${VIF_LLM_PORT:-6545}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=============================================="
echo " VIF 集成测试(真实模型)"
echo " STT=$STT_PORT  LLM=$LLM_PORT"
echo "=============================================="

# 1. 启动 LLM 服务
echo "[1/4] 启动 LLM 服务 (:${LLM_PORT})..."
VIF_LLM_PORT="$LLM_PORT" python -m services.llm_server &
LLM_PID=$!
trap 'kill $LLM_PID $STT_PID 2>/dev/null || true' EXIT

# 2. 启动 STT 服务
echo "[2/4] 启动 STT 服务 (:${STT_PORT})..."
VIF_STT_PORT="$STT_PORT" python -m services.stt_server &
STT_PID=$!

# 3. 等待就绪
echo "[3/4] 等待服务就绪(最多 180s)..."
for i in $(seq 1 36); do
    if curl -sf "http://localhost:${STT_PORT}/health" >/dev/null 2>&1 \
       && curl -sf "http://localhost:${LLM_PORT}/health" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 $STT_PID 2>/dev/null || ! kill -0 $LLM_PID 2>/dev/null; then
        echo "❌ 服务启动失败(进程退出)"
        exit 1
    fi
    sleep 5
done

# 4. 运行集成测试
echo "[4/4] 运行集成测试..."
STT_HOST=localhost STT_PORT=$STT_PORT LLM_HOST=localhost LLM_PORT=$LLM_PORT \
    python -m pytest tests/test_e2e.py tests/test_api_endpoints.py \
    -v -m integration --tb=short

echo "✅ 集成测试完成(退出码 $?)"
