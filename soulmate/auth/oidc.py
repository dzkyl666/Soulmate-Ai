"""OIDC（OpenID Connect）适配：把 Streamlit 原生的 `st.login()` 接进来。

【前提】
1. Streamlit >= 1.43 提供 `st.login()` / `st.logout()`（本机 1.63 有）；
2. 需要在 `.streamlit/secrets.toml` 里配好一个 OIDC provider（[auth] 段），
   详见 docs/DEPLOYMENT.md。
3. `SOULMATE_AUTH_MODE=oidc|hybrid` 才走这里。

【为什么还要本机账号库】
OIDC 登录拿到的只是「一个稳定身份」，项目的数据是按用户目录隔离的，
每个 OIDC 用户也要对应一个本机 UserRecord（密码留空、标 oidc_sub）。
这样管理员管理、停用、诊断都走同一套接口。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any


def oidc_configured() -> bool:
    """secrets.toml 里有没有 [auth] 段（有才能用 st.login）。"""
    import streamlit as st

    try:
        return bool(st.secrets.get("auth"))
    except Exception:
        return False


def run_login() -> None:
    """调起 Streamlit 原生登录 UI。未登录时调用后页面会停在登录视图。"""
    import streamlit as st

    st.login()


def logout() -> None:
    import streamlit as st

    st.logout()


def current_identity() -> dict[str, Any] | None:
    """返回当前 OIDC 身份（dict），未登录返回 None。

    st.user 是 UserInfoProxy：登录后 to_dict() 有 is_logged_in=True。
    """
    import streamlit as st

    try:
        info = st.user.to_dict()
    except Exception:
        return None
    if not isinstance(info, dict) or not info.get("is_logged_in"):
        return None
    return info


def stable_username(identity: dict[str, Any]) -> str:
    """把 OIDC 身份映射成安全的本机用户名（只能含字母数字下划线短线）。

    - 优先用邮箱（小写、去掉 @ 后截断 + 哈希尾巴，避开 Windows 文件名非法字符）；
    - 没有邮箱就用 sub / 随机串。
    永远以 `oidc_` 前缀区分，避免和本机注册的用户名撞车。
    """
    email = str(identity.get("email") or "").strip().lower()
    sub = str(identity.get("sub") or "").strip()
    raw = email or sub
    if not raw:
        raw = uuid.uuid4().hex
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    base = "".join(c for c in raw.split("@")[0][:24] if c.isalnum() or c in "_-.").strip(".")
    base = base or "user"
    return f"oidc_{base}_{digest}"


def display_name(identity: dict[str, Any]) -> str:
    return str(identity.get("email") or identity.get("name") or identity.get("sub") or "")[:60]