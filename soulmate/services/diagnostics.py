"""系统诊断：把「这个实例是否健康」变成一张数据表。

给诊断页用，也适合接到监控（输出是纯 dict，转 JSON 即事件上报）。
不调用任何模型、不读写用户数据 —— 纯只读巡检，失败也只是给个字段值。
"""

from __future__ import annotations

from pathlib import Path

import openai
import streamlit

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


def collect(settings: Settings, *, users_count: int = 0, providers_count: int = 0, user_id: str = "") -> dict:
    """收集诊断信息（不做任何写操作）。"""
    import sys

    startup_problems = settings.validate_for_startup() if settings.is_production else []

    return {
        "environments": {
            "env": settings.env,
            "debug": settings.debug,
            "auth_mode": settings.auth_mode,
            "allow_signup": settings.allow_signup,
        },
        "versions": {
            "python": sys.version.split()[0],
            "soulmate": soulmate.__version__,
            "streamlit": streamlit.__version__,
            "openai": openai.__version__,
        },
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