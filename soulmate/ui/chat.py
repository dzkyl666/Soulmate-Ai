"""聊天主区：伴侣头部 + 消息列表 + 流式输入。

【本版相比旧版的关键改进】
1. **流式失败不丢回复**：可恢复的「半截回复」会作为 assistant 消息落盘，
   不会出现「用户消息已保存、AI 回复没了」的尴尬；
2. **降级链**：主模型中断自动切备选模型（UI 上提示「已切换」）；
3. **失败重试**：整段失败时原地给「↻ 重试」，不用重打一遍；
4. **上下文预算**：发送前按字符预算裁剪历史，防 token 烧光。
"""

from __future__ import annotations

import streamlit as st

from soulmate.core.exceptions import ValidationError
from soulmate.core.models import ChatMessage
from soulmate.core.settings import Settings
from soulmate.llm.types import ChunkEvent, EndEvent, ErrorEvent, FallbackEvent
from soulmate.services.container import ServiceContainer
from soulmate.ui import dialogs


def _log(ctx: str, msg: str) -> None:
    import logging

    logging.getLogger("soulmate.ui.chat").info("[%s] %s", ctx, msg)


def _load_current(messages: list[ChatMessage]) -> None:
    messages.clear()


def render_chat(svc: ServiceContainer, settings: Settings) -> None:
    companions = svc.companions.list()
    if not companions:
        _render_onboarding(svc)
        return

    # ── 当前伴侣 ──
    cid = st.session_state.get("_companion_id")
    if cid not in [c.id for c in companions]:
        cid = companions[0].id
        st.session_state["_companion_id"] = cid
    companion = svc.companions.get(cid)
    provider = svc.provider_config(companion.provider_id)  # type: ignore[arg-type]
    fallback = svc.fallback_config(companion)  # type: ignore[arg-type]
    profile = svc.profile()

    # ── 当前会话 ──
    metas = svc.companions.list_metas(cid)
    sid = st.session_state.get("_session_id")
    if sid not in [m.session_id for m in metas]:
        if metas:
            sid = metas[0].session_id
        else:
            sid = svc.companions.new_session_id()
        st.session_state["_session_id"] = sid

    # 会话切换了就重载消息
    if st.session_state.get("_loaded_key") != (cid, sid):
        st.session_state["_messages"] = svc.companions.load_messages(cid, sid)
        st.session_state["_loaded_key"] = (cid, sid)
    messages: list[ChatMessage] = st.session_state["_messages"]

    # ── 头部：标题 + AI 起名 + 改名 ──
    st.markdown(f"#### {companion.avatar} {companion.name}")
    meta = f"会话：{svc.companions.session_title(cid, sid) or '新会话'}　｜　模型：{provider.label if provider else '未绑定'}"
    if fallback:
        meta += f"　｜　备选：{fallback.label}"
    if companion.purpose:
        meta += f"　｜　用途：{companion.purpose}"
    st.caption(meta)
    c_title, c_ai, c_rename = st.columns([6, 1, 1])
    with c_ai:
        if st.button("✨ AI 起名", width="stretch", help="让伴侣给会话起个标题", key="ai_title_btn"):
            if not messages:
                st.toast("先聊两句再起名吧～")
            else:
                try:
                    with st.spinner("TA 正在想标题…"):
                        title = svc.companions.generate_ai_title(provider, fallback, messages)
                    if title:
                        svc.companions.save_messages(cid, sid, messages, title=title, title_source="ai")
                        st.rerun()
                        return
                    st.warning("模型没返回有效的标题，再试一次？")
                except ValidationError as exc:
                    st.error(exc.user_message())
                except Exception as exc:  # noqa: BLE001 - UI 兜底
                    _log("ai_title", str(exc))
                    st.error("起名失败，请稍后重试")
    with c_rename:
        if st.button("✏️ 改名", width="stretch", help="手动改会话名", key="rename_btn"):
            dialogs.rename_session_dialog(sid, svc.companions.session_title(cid, sid) or "新会话")

    # ── 未绑定模型 → 引导 ──
    if not provider:
        st.warning(f"⚠️ **{companion.name}** 还没绑定模型服务 —— 伴侣是「人设」，模型才是「大脑」。")
        c1, c2 = st.columns([1, 2.6], vertical_alignment="center")
        with c1:
            if st.button("⚡ 一键绑定模型服务", type="primary", width="stretch", key="bind_quick"):
                dialogs.edit_companion_dialog(companion.model_dump())
        with c2:
            st.caption("也可以在侧边栏「当前伴侣 → ✏️ 编辑这个伴侣」里补选。")
        st.chat_input("先给伴侣绑定模型服务才能聊天", disabled=True)
        return

    # ── 消息列表 ──
    if not messages:
        with st.chat_message(companion.name, avatar=companion.avatar):
            st.write(f"我是{companion.name}～想聊点什么？")
        st.caption("试试说：今天有点累 / 你在干嘛呀 / 给我讲个笑话")
    else:
        if st.session_state.get("_manage_mode"):
            _render_manage_messages(svc, companion.avatar, companion.name, profile, messages, cid, sid)
        else:
            _render_normal_messages(companion.avatar, companion.name, profile, messages, cid, sid, svc)

    # ── 输入区 ──
    prompt = st.chat_input("请输入你的问题", max_chars=settings.max_message_chars)
    if prompt:
        try:
            prompt = _validate_prompt(prompt, settings)
        except ValidationError as exc:
            st.error(exc.user_message())
            return
        _send_and_stream(svc, settings, companion, provider, fallback, profile, messages, cid, sid, prompt)
    elif st.session_state.get("_retry") and messages and messages[-1].role == "user":
        st.session_state["_retry"] = False
        _send_and_stream(svc, settings, companion, provider, fallback, profile, messages, cid, sid, None)


# ══════════════════════════════════════════════════════════
# 消息渲染
# ══════════════════════════════════════════════════════════
def _render_normal_messages(
    avatar: str, name: str, profile, messages: list[ChatMessage], cid: str, sid: str, svc: ServiceContainer
) -> None:
    for i, m in enumerate(messages):
        col_msg, col_del = st.columns([14, 1])
        with col_msg:
            is_ai = m.role == "assistant"
            st.chat_message(name if is_ai else (profile.nickname or "我"), avatar=avatar if is_ai else (profile.avatar or "🐶")).write(m.content)
        with col_del:
            st.write("")
            if st.button("", icon="🗑️", key=f"del_{i}", help="删除这条消息"):
                del messages[i]
                svc.companions.save_messages(cid, sid, messages)
                st.rerun()
                return


def _render_manage_messages(
    svc: ServiceContainer, avatar: str, name: str, profile, messages: list[ChatMessage], cid: str, sid: str
) -> None:
    selected = []
    for i, m in enumerate(messages):
        col_chk, col_msg = st.columns([1, 14])
        with col_chk:
            if st.checkbox("", key=f"chk_{i}", label_visibility="collapsed"):
                selected.append(i)
        with col_msg:
            is_ai = m.role == "assistant"
            st.chat_message(name if is_ai else (profile.nickname or "我"), avatar=avatar if is_ai else (profile.avatar or "🐶")).write(m.content)
    if selected:
        if st.button(f"🗑️ 删除选中的 {len(selected)} 条", type="primary"):
            for i in sorted(selected, reverse=True):
                del messages[i]
            svc.companions.save_messages(cid, sid, messages)
            st.rerun()


# ══════════════════════════════════════════════════════════
# 发送 + 流式
# ══════════════════════════════════════════════════════════
def _validate_prompt(prompt: str, settings: Settings) -> str:
    from soulmate.core.validation import validate_text

    return validate_text(prompt, field="消息", max_chars=settings.max_message_chars, allow_empty=False)


def _send_and_stream(
    svc: ServiceContainer,
    settings: Settings,
    companion,
    provider,
    fallback,
    profile,
    messages: list[ChatMessage],
    cid: str,
    sid: str,
    prompt: str | None,
) -> None:
    """真正的生成流程。prompt=None 表示「重试」：不新增用户消息，直接重新生成。"""
    if prompt:
        messages.append(ChatMessage(role="user", content=prompt))
        svc.companions.save_messages(cid, sid, messages)
        with st.chat_message(profile.nickname or "我", avatar=profile.avatar or "🐶"):
            st.write(prompt)

    # ── 系统提示词 = 人设 + 使用者信息 + 长期记忆（现拼）──
    mem_facts = svc.memory.list_facts(cid)
    mem_block = svc.memory.to_prompt_block(mem_facts)
    sys_prompt = svc.companions.build_system_prompt(companion, profile, mem_block)

    # ── 上下文预算裁剪：只带最近 N 条 + 总字符上限 ──
    history_len = st.session_state.get("_history_len", settings.default_history_length)
    budget = settings.max_context_chars
    history = svc.llm.trim_to_context_budget(messages[-history_len:], budget)

    # ── 流式渲染 ──
    partial: list[str] = []
    final_text: str | None = None
    st.session_state["_retry"] = False  # 防止无限重试

    with st.chat_message(companion.name, avatar=companion.avatar):
        placeholder = st.empty()
        for evt in svc.llm.stream(provider, history, sys_prompt, fallback_cfg=fallback):
            if isinstance(evt, ChunkEvent):
                partial.append(evt.delta)
                placeholder.markdown("".join(partial))
            elif isinstance(evt, FallbackEvent):
                partial.clear()
                placeholder.caption(f"主模型中断，已切换到 {evt.to_label} …")
                st.caption(f"（本回复由备选模型 {evt.to_label} 生成）")
            elif isinstance(evt, EndEvent):
                placeholder.markdown(evt.text)
                final_text = evt.text
                svc.metrics.record(
                    calls=1,
                    prompt_tokens=evt.usage.prompt_tokens,
                    completion_tokens=evt.usage.completion_tokens,
                )
            elif isinstance(evt, ErrorEvent):
                if partial:
                    # 半截回复：保留下来当 assistant 消息，不让用户白等
                    final_text = "".join(partial)
                    placeholder.markdown(final_text)
                    st.caption(f"⚠️ 回复中断（{evt.user_message}），已保留以上内容")
                else:
                    placeholder.error(f"❌ {evt.user_message}")
                    st.session_state["_retry"] = True

    # ── 收尾：落盘 + 记忆抽取 ──
    if final_text is not None:
        messages.append(ChatMessage(role="assistant", content=final_text))
        svc.companions.save_messages(cid, sid, messages)
        # 长期记忆抽取（可关），失败静默
        if st.session_state.get("_memory_enabled", True):
            svc.memory.remember_from_exchange(cid, messages, provider, fallback)
        st.session_state["_messages"] = messages
        st.rerun()
        return

    if st.session_state.get("_retry"):
        st.caption("点击上方「↻ 重试」，或直接重发消息。")
        if st.button("↻ 重试", type="secondary", key="retry_btn"):
            st.rerun()


# ══════════════════════════════════════════════════════════
# 空状态引导
# ══════════════════════════════════════════════════════════
def _render_onboarding(svc: ServiceContainer) -> None:
    st.subheader("👋 欢迎来到 Soulmate AI")
    if not svc.providers.list():
        st.write("**第一步：先接入一个模型服务。** 填入 Base URL / API Key / 模型名就能接入任意 OpenAI 兼容服务。")
        if st.button("➕ 添加模型服务", type="primary", key="onboard_add_p"):
            dialogs.add_provider_dialog()
            st.rerun()
        return
    st.write("**第二步：创建一个伴侣。** 给 TA 起名字、写人设、挑头像，并绑定模型服务。")
    if st.button("➕ 新建伴侣", type="primary", key="onboard_add_c"):
        dialogs.add_companion_dialog()
        st.rerun()


def handle_new_session(svc: ServiceContainer) -> None:
    """点「新建会话」：当前消息先落盘，然后开一个空会话。"""
    cid = st.session_state.get("_companion_id")
    if not cid:
        return
    svc.companions.save_messages(cid, st.session_state.get("_session_id") or "", st.session_state.get("_messages") or [])
    st.session_state["_session_id"] = svc.companions.new_session_id()
    st.session_state["_messages"] = []
    st.session_state["_loaded_key"] = (cid, st.session_state["_session_id"])


def handle_switch_companion(svc: ServiceContainer, companion_id: str) -> None:
    cid = st.session_state.get("_companion_id")
    if cid and cid != companion_id:
        svc.companions.save_messages(cid, st.session_state.get("_session_id") or "", st.session_state.get("_messages") or [])
    st.session_state["_companion_id"] = companion_id
    metas = svc.companions.list_metas(companion_id)
    st.session_state["_session_id"] = metas[0].session_id if metas else svc.companions.new_session_id()
    st.session_state["_messages"] = svc.companions.load_messages(companion_id, st.session_state["_session_id"])
    st.session_state["_loaded_key"] = (companion_id, st.session_state["_session_id"])


def handle_switch_session(svc: ServiceContainer, session_id: str) -> None:
    cid = st.session_state.get("_companion_id")
    st.session_state["_session_id"] = session_id
    st.session_state["_messages"] = svc.companions.load_messages(cid, session_id)
    st.session_state["_loaded_key"] = (cid, session_id)