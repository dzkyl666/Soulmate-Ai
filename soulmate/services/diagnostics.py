"""系统诊断：把「这个实例是否健康」变成一张数据表。

给诊断页用，也适合接到监控（输出是纯 dict，转 JSON 即事件上报）。
不调用任何模型、不读写用户数据 —— 纯只读巡检，失败也只是给个字段值。

【分层注记】
本模块刻意**不 import streamlit / openai**：那样会让 services 层反向依赖 UI 依赖。
第三方库的版本号由调用方（ui 层）通过 `extra_versions` 注入。
"""

from __future__ import annotations

import sys
from pathlib import Path

import soulmate
from soulmate.core.settings import Settings


def _dir_size(path: Path) -> str:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        return "?"
    if total < 1024:
        return f"{total} B"
    if total < 1024**2:
        return f"{total / 1024:.1f} KB"
    if total < 1024**3:
        return f"{total / 1024**2:.1f} MB"
    return f"{total / 1024**3:.1f} GB"


def collect(
    settings: Settings,
    *,
    users_count: int = 0,
    providers_count: int = 0,
    user_id: str = "",
    extra_versions: dict[str, str] | None = None,
) -> dict:
    """收集诊断信息（不做任何写操作）。

    `extra_versions` 由 UI 层传入第三方库版本（如 {"streamlit": ..., "openai": ...}），
    这样 services 层不必 import 它们。
    """
    startup_problems = settings.validate_for_startup() if settings.is_production else []

    versions = {
        "python": sys.version.split()[0],
        "soulmate": soulmate.__version__,
    }
    versions.update(extra_versions or {})

    return {
        "environments": {
            "env": settings.env,
            "debug": settings.debug,
            "auth_mode": settings.auth_mode,
            "allow_signup": settings.allow_signup,
        },
        "versions": versions,
        "data": {
            "root": str(settings.data_root()),
            "size": _dir_size(settings.data_root()),
            "users": users_count,
            "current_providers": providers_count,
            "current_user": user_id or "-",
        },
        "secrets": {
            "app_secret_set": bool(settings.app_secret),
            "app_secret_ok": bool(settings.app_secret) and len(settings.app_secret) >= 16,
        },
        "security": {
            "allow_private_base_url": settings.allow_private_base_url,
            "rate_limit_chat_per_minute": settings.rate_limit_chat_per_minute,
        },
        "startup_problems": startup_problems,
    }
