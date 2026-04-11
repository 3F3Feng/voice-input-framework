#!/usr/bin/env python3
"""
Voice Input Framework - Pytest 配置
"""
import sys
import os
from pathlib import Path

# 确保项目根目录在 Python 路径中
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test (requires conda env)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
