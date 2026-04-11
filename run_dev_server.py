#!/usr/bin/env python3
"""
Voice Input Framework - 开发服务器启动脚本

使用 mlx-test conda 环境运行开发服务器，支持 MLX 模型加速。
端口: 6544

使用方法:
    python run_dev_server.py
    # 或指定端口
    VIF_DEV_PORT=6555 python run_dev_server.py
"""

import os
import sys
import subprocess
from pathlib import Path

# 获取 mlx-test conda 环境的 Python
MLX_PYTHON = "/Users/shifengzhang/anaconda3/envs/mlx-test/bin/python"
CURRENT_SCRIPT = Path(__file__).resolve()

# 设置环境变量
os.environ.setdefault("VIF_HOST", "127.0.0.1")
os.environ.setdefault("VIF_PORT", "6544")
os.environ.setdefault("VIF_LOG_LEVEL", "DEBUG")

def main():
    """使用正确的 Python 环境启动服务器"""
    print("=" * 60)
    print("Voice Input Framework - 开发服务器")
    print("=" * 60)
    print(f"Python: {MLX_PYTHON}")
    print(f"地址: http://127.0.0.1:6544")
    print(f"API 文档: http://127.0.0.1:6544/docs")
    print("=" * 60)
    
    # 使用 subprocess 运行服务器，继承当前进程的环境
    result = subprocess.run(
        [MLX_PYTHON, str(CURRENT_SCRIPT)],
        cwd=str(CURRENT_SCRIPT.parent)
    )
    sys.exit(result.returncode)

if __name__ == "__main__":
    # 如果已经是用 mlx-test Python 运行，直接执行
    if sys.executable == MLX_PYTHON or "mlx-test" in sys.executable:
        # 导入并运行服务器
        import logging
        logging.basicConfig(level=logging.DEBUG)
        
        from server.api import main
        main()
    else:
        main()
