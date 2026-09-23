"""可选的访问令牌(F20)。

两个服务都没有鉴权,默认只绑回环地址。可一旦为了远程使用改成 `VIF_STT_HOST=0.0.0.0`,
局域网里谁都能调它 —— 包括改提示词、切模型、读个人词库。设置 `VIF_API_TOKEN` 之后,
除 `/health` 外的每个请求都必须带 `Authorization: Bearer <令牌>`(WebSocket 也可以用
`?token=` 查询参数,方便不能自定义请求头的客户端)。

不设就和以前完全一样:本机使用、由客户端拉起的服务都不需要它。
"""

from __future__ import annotations

import hmac
import os

#: 不需要令牌的路径。`/health` 只报状态,客户端要靠它判断「连不连得上」。
PUBLIC_PATHS = frozenset({"/health"})


def api_token() -> str | None:
    token = os.getenv("VIF_API_TOKEN", "").strip()
    return token or None


def token_ok(authorization: str | None, query_token: str | None = None) -> bool:
    """请求带的令牌对不对。没配置令牌时一律放行。"""
    expected = api_token()
    if expected is None:
        return True
    supplied = None
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    elif query_token:
        supplied = query_token
    # 常数时间比较,别让响应时间泄露令牌前缀对了几位。
    return supplied is not None and hmac.compare_digest(supplied, expected)


def needs_check(method: str, path: str) -> bool:
    return method != "OPTIONS" and path not in PUBLIC_PATHS


def outgoing_headers() -> dict[str, str]:
    """STT 服务转发给 LLM 服务时带上的头(两边用同一个令牌)。"""
    token = api_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


UNAUTHORIZED_MESSAGE = (
    "访问令牌缺失或不对(服务端设置了 VIF_API_TOKEN),请在客户端的远程连接里填上令牌"
)
