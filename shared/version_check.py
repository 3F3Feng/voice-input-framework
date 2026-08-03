"""
Voice Input Framework - Python 版本检查

依赖生态(numpy 1.26 / mlx / torch)尚未适配 Python 3.13/3.14:
- numpy<2.0 无 cp314 wheel → pip 会尝试源码编译并崩溃(std::ptrdiff_t 错误)
- 项目 requires-python = ">=3.11,<3.13"

生产入口(vif-run.py / run_client.py / services.*_server.main)启动时调用,
提前给出明确错误,避免用户用 3.14 建环境后 pip 源码编译崩溃。
测试环境不经过此检查(测试直接 import 模块)。
"""

import sys

MIN_VERSION = (3, 11)
# 含 3.12;3.13/3.14 无 numpy wheel
MAX_EXCLUSIVE = (3, 13)


def check_python_version() -> None:
    """校验 Python 版本,不符时 SystemExit 并给出可操作提示"""
    if sys.version_info < MIN_VERSION or sys.version_info >= MAX_EXCLUSIVE:
        current = sys.version.split()[0]
        raise SystemExit(
            "Voice Input Framework 需要 Python 3.11-3.12,"
            f"当前是 {current}。\n"
            "依赖(numpy 1.26/mlx/torch)尚无 Python 3.13/3.14 的预编译包,"
            "pip 会尝试源码编译并失败。\n"
            "请用 Python 3.11 或 3.12 重建环境:\n"
            "  python3.12 -m venv .venv && source .venv/bin/activate\n"
            "  pip install -r requirements-stt.txt\n"
            "查看可用版本: python3.12 --version 或 ls /usr/local/bin/python3*"
        )
