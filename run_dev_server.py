#!/usr/bin/env python3
"""
Voice Input Framework - 开发服务器启动脚本

在 6544 端口运行开发服务器，用于本地测试。
使用方法:
    python run_dev_server.py
    # 或指定端口
    VIF_DEV_PORT=6555 python run_dev_server.py
"""

import os
import sys
import logging
from pathlib import Path

# 添加项目路径
project_dir = Path(__file__).parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

# 设置开发环境变量
os.environ.setdefault("VIF_HOST", "127.0.0.1")
os.environ.setdefault("VIF_PORT", "6544")
os.environ.setdefault("VIF_LOG_LEVEL", "DEBUG")

# 导入并运行
from server.api import main

if __name__ == "__main__":
    print("=" * 60)
    print("Voice Input Framework - 开发服务器")
    print("=" * 60)
    print(f"地址: http://127.0.0.1:6544")
    print(f"API 文档: http://127.0.0.1:6544/docs")
    print("=" * 60)
    
    main()
