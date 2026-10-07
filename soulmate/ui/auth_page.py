"""登录 / 注册 / 首次初始化管理员 页面。

【三种形态】
- 一个用户都没有 → 「创建管理员账号」（bootstrap，首个用户自动 admin）
- 有用户 → 「登录」，若开放注册再加「注册」标签
- 返回登录成功的 username，由 app.py 写入 session_state 完成登录

【★ UI 边界的异常策略：统一捕 `SoulmateError`，不逐个捕具体子类】
见 `soulmate/ui/boundary.py` 的模块 docstring（含一次真实事故的复盘）。
简言之：`RateLimitError` 不是 `AuthError` 的子类，逐个捕会让"连点登录触发限流"
直接冒到 Streamlit 变成整页红框。本页三个表单统一走 `boundary.guard()`。
"""

from __future__ import annotations

import streamlit as st

from soulmate.core.logging import get_logger
from soulmate.core.settings import Settings
from soulmate.services.auth_service import AuthService
from soulmate.ui.boundary import guard

log = get_logger("soulmate.ui.auth_page")


def _store(settings: Settings) -> AuthService:
    """拿认证门面（不再是 UserStore + UserRepository —— 那样会 ui→storage 越层）。"""
    return AuthService(settings)


def _login_flow(settings: Settings) -> None:
    """登录表单。成功后设置 session_state['user_id'] 并 rerun。"""
    st.markdown("### 🔐 欢迎回来")
    username = st.text_input("用户名", key="login_user", autocomplete="username")
    password = st.text_input("密码", type="password", key="login_pass", autocomplete="current-password")
    if st.button("登录", type="primary", width="stretch", key="login_submit"):
        user = guard(lambda: _store(settings).authenticate(username, password), what="登录")
        if user is None:
            return
        st.session_state["user_id"] = user.username
        st.rerun()


def _register_flow(settings: Settings) -> None:
    store = _store(settings)
    st.markdown("### ✨ 注册新账号")
    username = st.text_input("用户名（3-32 位，字母/数字/._-）", key="reg_user")
    password = st.text_input(
        "密码（至少 8 位，含大小写和数字）", type="password", key="reg_pass",
        autocomplete="new-password",
    )
    confirm = st.text_input("确认密码", type="password", key="reg_confirm", autocomplete="new-password")
    if st.button("注册并登录", type="primary", width="stretch", key="register_submit"):
        if password != confirm:
            st.error("两次输入的密码不一致")
            return
        user = guard(lambda: store.register(username, password), what="注册")
        if user is None:
            return
        st.session_state["user_id"] = user.username
        st.rerun()


def render_login_page(settings: Settings) -> bool:
    """渲染登录页。返回是否已登录。"""
    store = _store(settings)

    st.title("🧸 Soulmate AI")
    st.caption("多伴侣 · 多模型 · 多会话 · 数据在你自己的服务器上")

    if store.needs_bootstrap():
        st.markdown("### 👋 首次使用：创建管理员账号")
        st.info("第一个账号自动拥有管理员权限。之后可以在这里或命令行开新号。")
        username = st.text_input("管理员用户名", key="boot_user")
        password = st.text_input("密码", type="password", key="boot_pass", autocomplete="new-password")
        confirm = st.text_input("确认密码", type="password", key="boot_confirm", autocomplete="new-password")
        if st.button("创建并进入", type="primary", width="stretch", key="bootstrap_submit"):
            if password != confirm:
                st.error("两次输入的密码不一致")
                return False
            user = guard(lambda: store.bootstrap(username, password), what="初始化管理员")
            if user is None:
                return False
            st.session_state["user_id"] = user.username
            st.session_state["_first_run"] = True
            st.rerun()
            return False

        st.markdown("---")
        st.markdown(
            "> **部署提示**：服务器部署会检查 `SOULMATE_APP_SECRET` 与 `SOULMATE_ENV=production`，"
            "详见 `docs/DEPLOYMENT.md`。"
        )
        return False

    tab_login, tab_reg = st.tabs(["登录", "注册"])
    with tab_login:
        _login_flow(settings)
    with tab_reg:
        if settings.allow_signup:
            _register_flow(settings)
        else:
            st.caption("当前未开放自助注册，请联系管理员开号")

    return bool(st.session_state.get("user_id"))
