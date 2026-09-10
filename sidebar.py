"""
侧边栏
======
对标参考项目 04-multibot 的 custom_pages/utils/sidebar.py：
所有"切换 / 列表 / 设置"都收在这里，main.py 只管聊天主区。

四个折叠块（用 expander 保持侧边栏整洁）：
    我的伴侣   —— 点谁切谁；🗑 删伴侣；➕ 新建伴侣
    会话历史   —— 只显示"当前伴侣"的会话，伴侣之间互不干扰
    模型服务   —— 模型服务池：改 / 删 / 加
    聊天设置   —— 携带历史条数 + 批量管理模式开关
"""
import streamlit as st

from theme import THEMES, apply_theme
from dialogs import (
    add_provider, edit_provider, confirm_delete_provider,
    add_companion, edit_companion, confirm_delete_companion,
    confirm_delete_session, rename_session, reset_form_keys,
    edit_profile,
)


# ══════════════════════════════════════════════════
# 回调（on_click 用）：只改状态，界面会自动重跑
# ══════════════════════════════════════════════════
def switch_companion(companion_id):
    """切换伴侣 → 自动打开它最近的会话（没有就开一个新的）"""
    mgr = st.session_state["manager"]
    mgr.switch_companion(companion_id)
    st.session_state["messages"] = mgr.load_messages()


def new_session():
    """新建会话：当前会话有内容就先存盘，然后开一个空的（不写盘，等第一条消息）"""
    mgr = st.session_state["manager"]
    if st.session_state.get("messages"):
        mgr.save_session(st.session_state["messages"])
    mgr.current_session_id = mgr.new_session_id()
    mgr.pending_session = True     # 用户主动新建 → 立刻显示在侧边栏
    mgr.manage_mode = False
    st.session_state["messages"] = []


def load_session(session_id):
    mgr = st.session_state["manager"]
    mgr.current_session_id = session_id
    mgr.pending_session = False
    mgr.manage_mode = False
    st.session_state["messages"] = mgr.load_messages(session_id)


# ══════════════════════════════════════════════════
# 侧边栏主体
# ══════════════════════════════════════════════════
def render_sidebar():
    mgr = st.session_state["manager"]

    with st.sidebar:
        st.subheader("AI 控制面板")

        # ── 主题配色 ──
        # st._config.set_option 改色后，前端要「下一次 rerun」才生效，
        # 所以 on_change 里改完要补一次 st.rerun()，否则框和页面对不上。
        def on_theme_change():
            apply_theme(st.session_state["theme_name"])
            st.session_state["_theme_changed"] = True

        st.selectbox("主题配色", options=list(THEMES), key="theme_name", on_change=on_theme_change)
        if st.session_state.get("_theme_changed"):
            st.session_state["_theme_changed"] = False   # 复位，防止无限 rerun
            st.rerun()

        # ── 我的伴侣 ──
        with st.expander("我的伴侣", expanded=True):
            if not mgr.companions:
                st.caption("还没有伴侣，点下面创建一个吧")
            for c in mgr.companions:
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.button(
                        f"{c.get('avatar', '')} {c['name']}",
                        width="stretch",
                        key=f"pick_{c['id']}",
                        type="primary" if c["id"] == mgr.current_companion_id else "secondary",
                        help=c.get("purpose") or "（未填用途）· 点这里切换",
                        on_click=switch_companion,
                        args=(c["id"],),
                    )
                with col2:
                    if st.button("", icon="🗑️", key=f"delc_{c['id']}", help="删除这个伴侣"):
                        confirm_delete_companion(c)

            if not mgr.providers:
                # 一个模型服务都没有时，伴侣建了也绑不上模型 → 引导先去上面加
                st.caption("⬆️ 先在上面「模型服务」里添加一个，才能创建伴侣")
            elif st.button("➕ 新建伴侣", width="stretch",
                           type="secondary" if mgr.companions else "primary"):
                reset_form_keys("add_c")     # 清掉上次残留的填写内容
                add_companion()

        # ── 会话历史（只属于当前伴侣）──
        with st.expander("会话历史", expanded=True):
            if not mgr.companions:
                st.caption("先创建一个伴侣")
            else:
                cc = mgr.current_companion()
                st.caption(f"当前：{cc.get('avatar', '')} {cc['name']}" if cc else "")
                st.button("新建会话", width="stretch", icon="➕", on_click=new_session)
                for s in mgr.list_sessions():
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        st.button(
                            s["title"],
                            width="stretch",
                            icon="📄",
                            key=f"load_{s['session_id']}",
                            type="primary" if s["session_id"] == mgr.current_session_id else "secondary",
                            help=f"共 {s['count']} 条消息",
                            on_click=load_session,
                            args=(s["session_id"],),
                        )
                    with col2:
                        if st.button("", icon="❌", key=f"dels_{s['session_id']}",
                                     help="删除这个会话"):
                            confirm_delete_session(s["session_id"], s["title"])

        # ── 模型服务 ──
        with st.expander("模型服务", expanded=not mgr.providers):
            if not mgr.providers:
                st.caption("还没有模型服务，先添加一个")
            for p in mgr.providers:
                col1, col2 = st.columns([5, 1])
                with col1:
                    key_tail = "（已填）" if p.get("api_key") else "（走环境变量）"
                    if st.button(
                        f"⚙️ {p.get('alias') or p.get('model')}",
                        width="stretch",
                        key=f"editp_{p['id']}",
                        help=f"模型：{p.get('model')}\n地址：{p.get('base_url')}\nKey：{key_tail}",
                    ):
                        reset_form_keys(f"edit_p_{p['id']}")
                        edit_provider(p)
                with col2:
                    if st.button("", icon="🗑️", key=f"delp_{p['id']}", help="删除这个模型服务"):
                        confirm_delete_provider(p)

            if st.button("➕ 添加模型服务", width="stretch",
                         type="secondary" if mgr.providers else "primary"):
                reset_form_keys("add_p")     # 清掉上次残留的填写内容
                add_provider()

        # ── 聊天设置 ──
        with st.expander("聊天设置"):
            mgr.history_length = st.slider(
                "携带对话条数", min_value=1, max_value=30,
                value=mgr.history_length,
                help="每次请求最多带多少条历史消息给模型（太多会烧 token）",
            )
            mgr.manage_mode = st.toggle(
                "管理消息（批量删除）", value=mgr.manage_mode,
                help="打开后每条消息前会出现勾选框，可以一次删多条",
            )

        # ── 我的资料（全局一份，所有伴侣共用）──
        st.divider()
        if mgr.user_has_profile():
            st.caption(f"👤 我：{mgr.user_avatar()} **{mgr.user_nickname()}**")
        else:
            st.caption(f"👤 我：{mgr.user_avatar()} **{mgr.user_nickname()}**　（点下面设置）")
        if st.button("👤 编辑我的资料", width="stretch", key="edit_profile_btn",
                     help="设置你自己的头像和昵称；所有伴侣、所有会话共用这一份"):
            reset_form_keys("prof")     # 清掉上次残留的填写内容
            edit_profile()

        # ── 伴侣信息速览 ──
        cc = mgr.current_companion()
        if cc:
            st.divider()
            st.caption(f"{cc.get('avatar', '')} **{cc['name']}**")
            if cc.get("purpose"):
                st.caption(f"用途：{cc['purpose']}")
            st.caption(f"模型：{mgr.provider_label(cc.get('provider_id'))}")
            if st.button("✏️ 编辑这个伴侣", width="stretch", key="edit_current_companion"):
                reset_form_keys(f"edit_c_{cc['id']}")
                edit_companion(cc)
