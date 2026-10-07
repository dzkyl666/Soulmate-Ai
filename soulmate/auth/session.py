"""登录态会话 token：签发与校验（HMAC 签名，无服务端存储）。

【为什么用无状态 token 而不是服务端 session 表】
单机部署没有数据库，也不想引入 Redis。HMAC 签名 token 自带有效期，服务端
只要不泄漏 `app_secret` 就无法伪造 —— 对单进程/单机应用足够。

【边界】
token 是「谁」的证据，不是「权限清单」。改权限（停用账号）后已签发的 token
最长还能用到 TTL 到期 —— 要求管理员接受这个窗口；
若要立刻生效，停用时把 `session_ttl_hours` 调小或重启服务。
"""

from __future__ import annotations

from soulmate.core.security import sign_token, verify_token
from soulmate.core.settings import Settings


def issue_session_token(username: str, settings: Settings) -> str:
    """给用户签发一个登录 token。"""
    return sign_token(
        {"u": username},
        settings.app_secret,
        ttl_seconds=settings.session_ttl_hours * 3600,
    )


def read_session_token(token: str, settings: Settings) -> str | None:
    """校验登录 token，返回用户名；无效/过期返回 None。"""
    payload = verify_token(
        token,
        settings.app_secret,
        ttl_seconds=settings.session_ttl_hours * 3600,
    )
    if not payload:
        return None
    username = str(payload.get("u") or "")
    return username or None
