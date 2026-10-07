"""侧边栏：页面切换 + 主题 + 伴侣/会话/模型服务/聊天设置/长期记忆。

与旧版最大区别：所有状态都在 st.session_state 或服务容器里，
任何按钮都只调用「处理函数」，主区在同一轮 rerun 里显示新状态。
"""

from __future__ import annotations

import streamlit as st

from soulmate.auth import oidc
from soulmate.core.security import mask_secret
from soulmate.core.settings import Settings
from soulmate.services.container import ServiceContainer
from soulmate.ui import dialogs, theme as ui_theme


def _nav_radio() -> str:
    """页面切换（聊天/设置/诊断），单选按钮保持在同一位置。"""
    page = st.radio(
        "页面",
        options=["💬 聊天", "⚙️ 设置", "🩺 诊断"],
        key="_page_radio",
        horizontal=False,
        label_visibility="collapsed",
    )
    mapping = {"💬 聊天": "chat", "⚙️ 设置": "settings", "🩺 诊断": "diagnostics"}
    return mapping[page]


def _theme_select(svc: ServiceContainer) -> None:
    """主题下拉：切换时应用 + 写入 profile（重启后仍保留）。"""
    profile = svc.profile()
    current = ui_theme.current_theme_name(ui_theme.DEFAULT_THEME)

    def _on_change() -> None:
        name = st.session_state["theme_name"]
        ui_theme.apply_theme(name)
        svc.profile_repo.update(theme=name)
        st.session_state["_theme_applied"] = True

    st.selectbox(
        "主题配色",
        options=list(ui_theme.THEMES),
        index=list(ui_theme.THEMES).index(current) if current in ui_theme.THEMES else 0,
        key="theme_name",
        on_change=_on_change,
    )


def render_sidebar(svc: ServiceContainer, settings: Settings, user_id: str) -> str:
    """渲染侧边栏，返回用户选的页面名（'chat' | 'settings' | 'diagnostics'）。"""
    with st.sidebar:
        st.subheader("🧸 Soulmate AI")
        page = _nav_radio()
        _theme_select(svc)

        # ── 我的伴侣 ──
        with st.expander("我的伴侣", expanded=True):
            companions = svc.companions.list()
            if not companions:
                st.caption("还没有伴侣，点下面创建一个吧")
            for c in companions:
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.button(
                        f"{c.avatar or '🧸'} {c.name}",
                        width="stretch",
                        key=f"pick_{c.id}",
                        type="primary" if c.id == st.session_state.get("_companion_id") else "secondary",
                        help=c.purpose or "（未填用途）· 点这里切换",
                        on_click=lambda cid=c.id: _switch_companion(svc, cid),
                    )
                with col2:
                    if st.button("", icon="🗑️", key=f"delc_{c.id}", help="删除这个伴侣"):
                        dialogs.delete_companion_dialog(c.model_dump())

            if svc.providers.list():
                if st.button("➕ 新建伴侣", width="stretch", type="secondary" if companions else "primary", key="add_companion_btn"):
                    dialogs.reset_form_keys("add_c")
                    dialogs.add_companion_dialog()
                    st.rerun()
            else:
                st.caption("⬆️ 先在下面「模型服务」里添加一个，才能创建伴侣")

        # ── 会话历史 ──
        cid = st.session_state.get("_companion_id")
        with st.expander("会话历史", expanded=True):
            if not cid:
                st.caption("先创建一个伴侣")
            else:
                c_name = svc.companions.get(cid).name if svc.companions.get(cid) else ""
                st.caption(f"当前：{c_name or ''}")
                st.button("➕ 新建会话", width="stretch", icon="➕", key="new_session_btn", on_click=lambda: _new_session(svc))
                for s in svc.companions.list_metas(cid):
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        st.button(
                            s.title,
                            width="stretch",
                            icon="📄",
                            key=f"load_{s.session_id}",
                            type="primary" if s.session_id == st.session_state.get("_session_id") else "secondary",
                            help=f"共 {s.count} 条消息",
                            on_click=lambda sid=s.session_id: _switch_session(svc, sid),
                        )
                    with col2:
                        if st.button("", icon="❌", key=f"dels_{s.session_id}", help="删除这个会话"):
                            dialogs.delete_session_dialog(s.session_id, s.title)

        # ── 模型服务 ──
        with st.expander("模型服务", expanded=not svc.providers.list()):
            if not svc.providers.list():
                st.caption("还没有模型服务，先添加一个")
            for p in svc.providers.list():
                col1, col2 = st.columns([5, 1])
                with col1:
                    key_tail = mask_secret(p.api_key) if p.api_key else "（走环境变量）"
                    if st.button(
                        f"⚙️ {p.alias or p.model}",
                        width="stretch",
                        key=f"editp_{p.id}",
                        help=f"模型：{p.model}\n地址：{p.base_url}\nKey：{key_tail}",
                    ):
                        dialogs.reset_form_keys(f"edit_p_{p.id}")
                        dialogs.edit_provider_dialog(p.model_dump())
                        st.rerun()
                with col2:
                    if st.button("", icon="🗑️", key=f"delp_{p.id}", help="删除这个模型服务"):
                        dialogs.delete_provider_dialog(p.model_dump())

            if st.button("➕ 添加模型服务", width="stretch", type="secondary" if svc.providers.list() else "primary", key="add_provider_btn"):
                dialogs.reset_form_keys("add_p")
                dialogs.add_provider_dialog()
                st.rerun()

        # ── 聊天设置 ──
        with st.expander("聊天设置"):
            st.session_state["_history_len"] = st.slider(
                "携带对话条数",
                min_value=1,
                max_value=settings.max_history_length,
                value=st.session_state.get("_history_len", settings.default_history_length),
                help="每次请求最多带多少条历史给模型（太多烧 token）",
            )
            st.session_state["_manage_mode"] = st.toggle(
                "管理消息（批量删除）",
                value=st.session_state.get("_manage_mode", False),
                help="打开后每条消息前会出现勾选框",
            )
            st.session_state["_memory_enabled"] = st.toggle(
                "自动记住关于我的事",
                value=st.session_state.get("_memory_enabled", True),
                help="每轮对话后让模型挑出值得记住的事（多花一次小调用）",
            )

        # ── 长期记忆 ──
        with st.expander("🧠 TA 记得你的事", expanded=False):
            cid = st.session_state.get("_companion_id")
            if not cid:
                st.caption("先创建一个伴侣")
            else:
                facts = svc.memory.list_facts(cid)
                if not facts:
                    st.caption("还没记住什么。多聊几句，重要的信息会自动记下来。")
                for f in facts:
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        st.caption(f.text)
                    with col2:
                        if st.button("", icon="🗑️", key=f"forget_{f.id}", help="让 TA 忘掉这条"):
                            svc.memory.forget_fact(cid, f.id)
                            st.rerun()
                if facts:
                    if st.button("🧹 清空全部记忆", width="stretch", key="forget_all_btn"):
                        svc.memory.forget_all(cid)
                        st.rerun()

        # ── 我的资料 / 用户信息 / 退出 ──
        st.divider()
        profile = svc.profile()
        label = profile.nickname or "我"
        st.caption(f"👤 {profile.avatar or '🐶'} **{label}**（{user_id}）")
        if st.button("👤 编辑我的资料", width="stretch", key="edit_profile_btn"):
            dialogs.edit_profile_dialog()
            st.rerun()

        # 管理员快捷：进设置页管理账号
        if _is_admin(svc, user_id):
            st.caption("👑 管理员")

        if st.button("🚪 退出登录", width="stretch", key="logout_btn"):
            _do_logout(svc, user_id)
        return page


# ══════════════════════════════════════════════════════════
# 内部处理函数（on_click 回调只允许改 session_state / 服务）
# ══════════════════════════════════════════════════════════
def _switch_companion(svc: ServiceContainer, companion_id: str) -> None:
    from soulmate.ui.chat import handle_switch_companion

    handle_switch_companion(svc, companion_id)


def _switch_session(svc: ServiceContainer, session_id: str) -> None:
    from soulmate.ui.chat import handle_switch_session

    handle_switch_session(svc, session_id)


def _new_session(svc: ServiceContainer) -> None:
    from soulmate.ui.chat import handle_new_session

    handle_new_session(svc)


def _is_admin(svc: ServiceContainer, user_id: str) -> bool:
    from soulmate.auth.user_store import UserStore

    store = UserStore(svc.user_repo, svc.settings)
    return store.is_admin(user_id)


def _do_logout(svc: ServiceContainer, user_id: str) -> None:
    # 清掉本用户相关的会话状态
    for key in list(st.session_state.keys()):
        if key.startswith("__soulmate_") or key in (
            "user_id",
            "_companion_id",
            "_session_id",
            "_messages",
            "_loaded_key",
            "_manage_mode",
            "_history_len",
            "_memory_enabled",
            "_first_run",
            "_retry",
            "_theme_applied",
            "_theme_changed",
        ):
            del st.session_state[key]
    # OIDC 模式再走 st.logout()
    if oidc.oidc_configured() and svc.settings.auth_mode in ("oidc", "hybrid"):
        oidc.logout()
    st.rerun()