#!/usr/bin/env python3
"""
Voice Input Framework - GUI Client Launcher

快捷键驱动的语音输入客户端

Usage:
    python run_client.py              # 使用配置文件设置
    python run_client.py 10.0.0.1     # 指定服务器地址
    python run_client.py 10.0.0.1 6543  # 指定服务器地址和端口

Environment Variables:
    VIF_SERVER_HOST - 服务器地址（优先级高于配置文件）
    VIF_SERVER_PORT - 服务器端口（优先级高于配置文件）
"""

import sys
import os
from client import HotkeyVoiceInput
from client.config_manager import ConfigManager

if __name__ == "__main__":
    # 从配置文件读取默认值
    config = ConfigManager()
    default_host = config.server_host
    default_port = config.server_port
    
    # 环境变量可以覆盖配置文件
    host = os.getenv("VIF_SERVER_HOST", default_host)
    port_str = os.getenv("VIF_SERVER_PORT")
    port = int(port_str) if port_str else default_port
    
    # 命令行参数覆盖所有
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        try:
            port = int(sys.argv[2])
        except ValueError:
            print(f"Error: Port must be a number, got '{sys.argv[2]}'")
            sys.exit(1)
    
    print(f"🎤 Voice Input Framework")
    print(f"📍 Server: {host}:{port}")
    print(f"\nStarting GUI client...\n")
    
    # 创建并运行客户端
    client = HotkeyVoiceInput(server_host=host, server_port=port)
    
    try:
        client.run()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
