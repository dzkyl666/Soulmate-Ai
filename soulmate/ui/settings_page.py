"""设置页：资料编辑 · 会话导出 · 数据备份 · 用量统计 · 管理员面板。"""

from __future__ import annotations

from typing import Literal

import streamlit as st

from soulmate.core.exceptions import mask_secret
from soulmate.core.settings import Settings
from soulmate.services.container import ServiceContainer
from soulmate.ui import dialogs


def render_settings(svc: ServiceContainer, settings: Settings, user_id: str) -> None:
    st.subheader("⚙️ 设置")

    with st.expander("👤 我的资料", expanded=True):
        profile = svc.profile()
        st.write(f"昵称：**{profile.nickname or '（未设置）'}**　头像：{profile.avatar or '🐶'}　主题：**{profile.theme}**")
        if st.button("✏️ 编辑资料", key="settings_edit_profile"):
            dialogs.reset_form_keys("prof")
            dialogs.edit_profile_dialog()
            st.rerun()

    with st.expander("📤 导出当前会话", expanded=True):
        cid = str(st.session_state.get("_companion_id") or "")
        sid = str(st.session_state.get("_session_id") or "")
        companion = svc.companions.get(cid) if cid else None
        doc = svc.session_repo.load(cid, sid) if (cid and sid) else None
        if doc is None or not doc.messages or companion is None:
            st.caption("当前会话还没有内容，先去聊几句吧。")
        else:
            st.caption(f"「{doc.title or '新会话'}」· {len(doc.messages)} 条消息")
            md = svc.export.session_to_markdown(companion, doc)
            js = svc.export.session_to_json(doc)
            c1, c2 = st.columns(2)
            with c1:
                st.download_button("⬇️ 下载 Markdown", data=md.encode("utf-8"), file_name=f"会话-{doc.session_id}.md", mime="text/markdown")
            with c2:
                st.download_button("⬇️ 下载 JSON", data=js.encode("utf-8"), file_name=f"会话-{doc.session_id}.json", mime="application/json")

    with st.expander("💾 数据备份", expanded=True):
        st.caption(f"备份会保留最近 {settings.backup_keep} 份（位置：`{settings.backup_dir(user_id)}`）。")
        if st.button("🛟 立即备份我的全部数据", key="backup_now"):
            with st.spinner("正在备份…"):
                dest = svc.export.backup_user_data(svc.user_dir)
            st.success(f"已备份到 `{dest}`")

    with st.expander("📈 用量统计", expanded=True):
        summary = svc.metrics.summary()
        today = summary["today"]
        total = summary["total"]
        c1, c2 = st.columns(2)
        with c1:
            st.metric("今日调用", today.get("calls", 0))
            st.metric("今日 token（输入/输出）", f"{today.get('prompt', 0)} / {today.get('completion', 0)}")
        with c2:
            st.metric("累计调用", total.get("calls", 0))
            st.metric("累计 token（输入/输出）", f"{total.get('prompt', 0)} / {total.get('completion', 0)}")
        st.caption(f"记录了 {summary['days']} 天的使用情况（本地存储，最近 30 天）")

    # ── 管理员面板 ──
    store = svc.auth
    if store.is_admin(user_id):
        with st.expander("👑 管理员：账号管理", expanded=False):
            st.caption("自助注册关闭时，用这里开新号。")
            with st.form("admin_new_user"):
                nu = st.text_input("新用户名")
                npw = st.text_input("初始密码（至少 8 位，含大小写和数字）", type="password")
                role_options: list[Literal["user", "admin"]] = ["user", "admin"]
                role = st.selectbox("角色", options=role_options, index=0)
                if st.form_submit_button("创建用户"):
                    try:
                        u = store.admin_create_user(nu, npw, role=role)
                        st.success(f"已创建 {u.username}（{u.role}）")
                    except Exception as exc:
                        st.error(f"创建失败：{exc}")
            st.divider()
            st.caption("现有账号：")
            for u in store.list_users():
                col1, col2, col3 = st.columns([4, 1, 1])
                with col1:
                    st.write(f"{u.username} · {u.role}" + (" · 已停用" if u.disabled else ""))
                with col2:
                    if st.button("停用" if not u.disabled else "启用", key=f"toggle_{u.username}"):
                        if u.username == user_id:
                            st.warning("不能停用自己")
                        else:
                            store.set_disabled(u.username, not u.disabled)
                            st.rerun()
                with col3:
                    if st.button("删除", key=f"del_{u.username}"):
                        if u.username == user_id:
                            st.warning("不能删除自己")
                        else:
                            store.delete_user(u.username)
                            st.rerun()

    with st.expander("🔑 我的模型服务", expanded=False):
        for p in svc.providers.list_providers():
            key_display = mask_secret(p.api_key) if p.api_key else "（环境变量）"
            st.caption(f"**{p.alias or p.model}**　·　{p.model}　·　{key_display}")

    with st.expander("ℹ️ 关于", expanded=False):
        st.caption(f"Soulmate AI · 版本 {__import__('soulmate').__version__} · 数据目录 `{settings.data_root()}`")
