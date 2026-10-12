"""让健康检查不刷屏。

客户端每一两秒问一次 `/health`,uvicorn 每次都记一行访问日志。客户端只留服务日志的最后
几百行(「设置 → 日志」和「复制诊断信息」用的就是它),一分钟不到,有用的那几行——模型
加载到了哪、每次听写的耗时——就被挤出去了。

成功的 `/health` 不记;失败的(非 200)照常记,那正是要看的。
"""

from __future__ import annotations

import logging

HEALTH_PATH = "/health"


def is_routine_health_check(path: str, status_code: int) -> bool:
    """这条请求是不是一次成功的健康检查(不值得记)。"""
    return path.split("?", 1)[0] == HEALTH_PATH and status_code == 200


class _DropHealthChecks(logging.Filter):
    """过滤 uvicorn 的访问日志。它的参数是 (客户端地址, 方法, 路径, HTTP 版本, 状态码)。"""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        try:
            return not is_routine_health_check(str(args[2]), int(args[4]))
        except (TypeError, ValueError):
            return True


def install() -> None:
    """给 uvicorn 的访问日志装上过滤器。重复调用只装一次。

    uvicorn 启动时会重新配置自己的 logger(换 handler),但不动已有的 filter,所以在
    `uvicorn.run` 之前装就行。
    """
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _DropHealthChecks) for f in access.filters):
        access.addFilter(_DropHealthChecks())
