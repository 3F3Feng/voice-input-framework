#!/usr/bin/env python3
"""
Voice Input Framework - 开发服务器启动脚本

使用 mlx-test conda 环境运行开发服务器，支持 MLX 模型加速。
端口: 6544

前置条件:
    - conda 环境 'mlx-test' 已配置
    - 或使用以下命令创建:
      conda create -n mlx-test python=3.11
      conda activate mlx-test
      pip install mlx mlx-lm transformers

使用方法:
    conda run -n mlx-test python run_dev_server.py
    # 或指定端口
    VIF_DEV_PORT=6555 conda run -n mlx-test python run_dev_server.py
"""

import os
import sys
from pathlib import Path

# 设置开发环境变量
os.environ.setdefault("VIF_HOST", "127.0.0.1")
os.environ.setdefault("VIF_PORT", "6544")
os.environ.setdefault("VIF_LOG_LEVEL", "DEBUG")

# 添加项目路径
project_dir = Path(__file__).parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

if __name__ == "__main__":
    host = os.getenv("VIF_HOST", "127.0.0.1")
    port = os.getenv("VIF_PORT", "6544")
    print("=" * 60)
    print("Voice Input Framework - 开发服务器")
    print("=" * 60)
    print(f"Python: {sys.executable}")
    print(f"地址: http://{host}:{port}")
    print(f"API 文档: http://{host}:{port}/docs")
    print("=" * 60)
    
    from server.api import main
    main()
