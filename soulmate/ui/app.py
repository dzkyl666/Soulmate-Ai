"""应用编排：登录 → 迁移 → 主题 → 页面分发。

【为什么主流程这么短】
真正的内容都在各页面模块里；本文件只负责「顺序」：
    1. 配置自检（生产环境有问题直接红屏停）
    2. 认证（OIDC 或本机账号）
    3. 用户容器 + 老数据迁移（幂等）
    4. 主题（持久化偏好首屏生效）
    5. 路由到 聊天 / 设置 / 诊断
"""

from __future__ import annotations

import logging

import streamlit as st

from soulmate.auth import oidc
from soulmate.auth.user_store import UserStore
from soulmate.core.logging import get_logger, set_request_id, setup_logging
from soulmate.core.settings import get_settings
from soulmate.services import migration
from soulmate.services.container import get_container_singleton
from soulmate.storage.repositories import UserRepository
from soulmate.ui import theme as ui_theme
from soulmate.ui.auth_page import render_login_page
from soulmate.ui.chat import render_chat
from soulmate.ui.diagnostics_page import render_diagnostics
from soulmate.ui.settings_page import render_settings
from soulmate.ui.sidebar import render_sidebar

log = get_logger("soulmate.ui.app")


def run() -> None:
    settings = get_settings()
    setup_logging(logging.DEBUG if settings.debug else logging.INFO, str(settings.log_dir()))
    set_request_id()

    # ── 1. 生产配置自检（fail fast）──
    problems = settings.validate_for_startup()
    if problems:
        st.error("**生产配置有致命问题，拒绝启动：**")
        for p in problems:
            st.error(f" - {p}")
        st.caption("修改 .env / 环境变量后重启服务。详见 docs/DEPLOYMENT.md。")
        st.stop()

    # ── 2. 认证 ──
    user_id = _require_auth(settings)
    if user_id is None:
        st.stop()

    # ── 3. 用户容器 + 老数据迁移（幂等，每次启动都跑一遍也没关系）──
    svc = get_container_singleton(st.session_state, settings, user_id)
    st.session_state["__container"] = svc  # dialogs/侧边栏都从这里拿容器
    try:
        migration.run_all(settings, user_id, svc)
    except Exception:  # noqa: BLE001 - 迁移失败不能挡着用户用
        log.exception("迁移异常（跳过）")

    # ── 4. 主题：持久化偏好首屏生效 ──
    if ui_theme.apply_saved_theme(svc.profile().theme):
        st.rerun()
        return

    # ── 5. 页面分发 ──
    page = render_sidebar(svc, settings, user_id)
    if page == "settings":
        render_settings(svc, settings, user_id)
    elif page == "diagnostics":
        render_diagnostics(svc, settings, user_id)
    else:
        render_chat(svc, settings)


def _require_auth(settings):
    """返回已认证 user_id；未通过时渲染登录页并返回 None。"""
    oidc_usable = settings.auth_mode in ("oidc", "hybrid") and oidc.oidc_configured()
    store = UserStore(UserRepository(settings.data_root()), settings)

    if oidc_usable:
        oidc.run_login()
        identity = oidc.current_identity()
        if not identity:
            st.info("🔐 请登录后继续使用。")
            st.stop()
            return None
        username = oidc.stable_username(identity)
        store.ensure_oidc_user(
            username=username,
            oidc_sub=str(identity.get("email") or identity.get("sub") or ""),
            display_name=oidc.display_name(identity),
        )
        return username

    # 本机账号模式
    if "user_id" not in st.session_state:
        logged_in = render_login_page(settings)
        if not logged_in:
            return None
    return st.session_state.get("user_id")