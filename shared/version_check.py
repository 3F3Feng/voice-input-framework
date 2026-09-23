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
            "请在仓库根目录重建环境(脚本用 uv 自动准备 3.11/3.12 解释器,并按硬件挑依赖):\n"
            "  scripts/setup-env.sh\n"
            "  uv run python -m services.stt_server\n"
            # 以前这里指向 `pip install -r requirements-stt.txt`:那份清单无条件装 mlx,
            # Linux 上照做建出来的环境一 import 就报 libmlx.so 找不到。
            "需要 LLM 后处理时加 --llm(目前仅 Apple Silicon 可用)。"
        )
