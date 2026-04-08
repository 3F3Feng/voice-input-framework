#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "PySide6>=6.6.0",
#     "pynput>=1.7.6",
#     "pyautogui>=0.9.54",
#     "pyperclip>=1.8.2",
#     "websockets>=12.0",
#     "httpx>=0.26.0",
#     "numpy>=1.26.0",
#     "sounddevice>=0.4.6",
# ]
# ///
"""
Voice Input Framework - GUI 客户端

使用 uv 运行:
    uv run run_gui.py

或者:
    uv run --with PySide6 --with pynput --with pyautogui --with pyperclip run_gui.py
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入 GUI 模块
from client.gui import main

if __name__ == "__main__":
    main()
