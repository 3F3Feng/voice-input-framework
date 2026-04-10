#!/usr/bin/env python3
"""
Voice Input Framework - Pytest 配置
"""

import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
