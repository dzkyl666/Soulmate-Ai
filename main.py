"""
AI 智能伴侣 —— 主程序（入口）
==============================
对标参考项目 04-multibot 的 app.py：入口只做三件事——
    1) 初始化（页面配置 / 主题 / 管理器）
    2) 画侧边栏（调 sidebar.render_sidebar）
    3) 画聊天主区

"伴侣怎么管、会话怎么存、模型怎么连"全在 companion_manager.py 里，
本文件不直接碰任何文件，只负责"把界面拼出来"。
"""
import streamlit as st

from theme import THEMES, apply_theme, current_theme_name
from companion_manager import CompanionManager
from sidebar import render_sidebar
from dialogs import add_provider, add_companion, rename_session, reset_form_keys, bind_provider

# ── 1. 页面配置（必须是第一个 streamlit 命令）──
st.set_page_config(
    page_title="AI智能伴侣",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={},
)

# ── 2. 主题：反查「服务器当前颜色」是哪一套，让下拉框和页面永远一致 ──
if "theme_name" not in st.session_state:
    st.session_state["theme_name"] = current_theme_name()

# ── 3. 管理器：整个应用的"数据大脑"，只在第一次运行时创建 ──
if "manager" not in st.session_state:
    _mgr = CompanionManager()
    _mgr.migrate_legacy_sessions()          # 一次性搬家：老版 session/*.json → data/sessions/
    if _mgr.companions:
        _mgr.switch_companion(_mgr.companions[0]["id"])
    st.session_state["manager"] = _mgr
    st.session_state["messages"] = _mgr.load_messages()

mgr = st.session_state["manager"]
if "messages" not in st.session_state:
    st.session_state["messages"] = mgr.load_messages()

# ── 4. 侧边栏 ──
render_sidebar()

# ── 5. 主区 ──
st.title("AI智能伴侣")
st.caption("🧸 你的专属 AI 伴侣 · 多伴侣 · 多模型 · 多会话")

# ── 引导第一步：一个模型服务都没有 ──
if not mgr.providers:
    st.divider()
    st.subheader("👋 第一步：先接入一个模型服务")
    st.write("填入 **Base URL / API Key / 模型名称** 就能接入任意 OpenAI 兼容的服务。"
             "选一个厂商预设，会自动帮你填好地址和常用模型名。")
    st.info("🔒 API Key 只保存在你自己电脑的 `data/providers.json` 里"
            "（已加进 `.gitignore`，不会上传到 GitHub）。")
    if st.button("➕ 添加模型服务", type="primary"):
        reset_form_keys("add_p")
        add_provider()
    st.stop()

# ── 引导第二步：还没有伴侣 ──
if not mgr.companions:
    st.divider()
    st.subheader("👋 第二步：创建一个伴侣")
    st.write("给 TA 起名字、写人设、挑头像，并绑定刚才的模型服务。")
    if st.button("➕ 新建伴侣", type="primary"):
        reset_form_keys("add_c")
        add_companion()
    st.stop()

# ── 兜底：有伴侣但没选中 ──
companion = mgr.current_companion()
if companion is None:
    mgr.switch_companion(mgr.companions[0]["id"])
    st.session_state["messages"] = mgr.load_messages()
    st.rerun()

provider = mgr.get_provider(companion.get("provider_id"))
messages = st.session_state["messages"]
assistant_avatar = companion.get("avatar") or "🧸"
assistant_name = companion["name"]
user_avatar = mgr.user_avatar()      # 来自「我的资料」：全局一份，换伴侣、换会话都不变
user_name = mgr.user_nickname()

# ══════════════════════════════════════════════════
# 顶部：当前伴侣 + 会话标题 + 起名 / 改名
# ══════════════════════════════════════════════════
st.divider()
c_title, c_ai, c_rename = st.columns([7, 1.2, 1])
with c_title:
    st.markdown(f"#### {assistant_avatar} {companion['name']}")
    meta = f"会话：{mgr.session_title()}　｜　模型：{mgr.provider_label(companion.get('provider_id'))}"
    if companion.get("purpose"):
        meta += f"　｜　用途：{companion['purpose']}"
    st.caption(meta)
with c_ai:
    if st.button("✨ AI 起名", width="stretch", help="让这个伴侣给当前会话起个标题"):
        if not messages:
            st.toast("先聊两句再起名吧～")
        else:
            try:
                with st.spinner("TA 正在想标题…"):
                    new_title = mgr.generate_ai_title(provider, messages)
                if new_title:
                    mgr.save_session(messages, title=new_title, title_source="ai")
                    st.rerun()
                else:
                    st.warning("模型没返回有效的标题，再试一次？")
            except Exception as e:
                st.error(f"❌ 起名失败：{e}")
with c_rename:
    if st.button("✏️ 改名", width="stretch", help="手动给会话改个名字"):
        rename_session(mgr.current_session_id, mgr.session_title())

# ══════════════════════════════════════════════════
# 消息列表
# ══════════════════════════════════════════════════
# 空状态引导：只在渲染时显示欢迎语，不写进 messages
# （一旦写进去，save_session 会认为"有内容"→ 立刻落盘 → 又冒出删不掉的空会话）
if not messages:
    with st.chat_message(assistant_name, avatar=assistant_avatar):
        st.write(f"我是{companion['name']}～想聊点什么？")
    st.caption("试试说：今天有点累 / 你在干嘛呀 / 给我讲个笑话")

if mgr.manage_mode:
    # ── 批量模式：每条前有勾选框，底部一个"删除选中 N 条" ──
    selected = []
    for i, m in enumerate(messages):
        col_chk, col_msg = st.columns([1, 14])
        with col_chk:
            if st.checkbox("选中", key=f"chk_{i}", label_visibility="collapsed"):
                selected.append(i)
        with col_msg:
            is_ai = m["role"] == "assistant"
            st.chat_message(assistant_name if is_ai else user_name,
                            avatar=assistant_avatar if is_ai else user_avatar).write(m["content"])
    if selected:
        if st.button(f"🗑️ 删除选中的 {len(selected)} 条", type="primary"):
            mgr.delete_messages(selected, messages)
            mgr.save_session(messages)
            st.rerun()
else:
    # ── 普通模式：每条消息右侧一个 🗑 一键删单条 ──
    for i, m in enumerate(messages):
        col_msg, col_del = st.columns([14, 1])
        with col_msg:
            is_ai = m["role"] == "assistant"
            st.chat_message(assistant_name if is_ai else user_name,
                            avatar=assistant_avatar if is_ai else user_avatar).write(m["content"])
        with col_del:
            st.write("")   # 把按钮往下压一点，跟气泡顶部对齐
            if st.button("", icon="🗑️", key=f"del_{i}", help="删除这条消息"):
                mgr.delete_messages([i], messages)
                mgr.save_session(messages)
                st.rerun()

# ══════════════════════════════════════════════════
# 输入区
# ══════════════════════════════════════════════════
if not provider:
    # 别让输入框凭空消失——用户会以为程序坏了。改成：说清缺什么 + 就地给入口 + 留个灰输入框。
    st.warning(f"⚠️ **{companion['name']}** 还没绑定模型服务 —— 伴侣是「人设」，模型才是「大脑」，"
               "缺了大脑就没法回话。")
    c_bind, c_hint = st.columns([1, 2.6], vertical_alignment="center")
    with c_bind:
        if st.button("⚡ 一键绑定模型服务", type="primary", width="stretch", key="quick_bind"):
            bind_provider(companion)
    with c_hint:
        st.caption("也可以点侧边栏最底部的「✏️ 编辑这个伴侣」，在弹窗第一项里选（效果一样）。")
    st.chat_input("先给这个伴侣绑定一个模型服务，才能开始聊天", disabled=True)
    st.stop()

if prompt := st.chat_input("请输入你的问题"):
    st.chat_message(user_name, avatar=user_avatar).write(prompt)
    messages.append({"role": "user", "content": prompt})
    mgr.save_session(messages)
    # 系统提示词 = 伴侣人设 + 占位符替换 + 「对方是谁」
    sys_prompt = mgr.build_system_prompt(companion)

    try:
        client = mgr.build_client(provider)
        history_messages = messages[-mgr.history_length:]     # 只带最近 N 条，防 token 暴涨
        response = client.chat.completions.create(
            model=provider.get("model"),
            messages=[{"role": "system", "content": sys_prompt}, *history_messages],
            stream=True,
        )

        def generate_response():
            for chunk in response:
                content = chunk.choices[0].delta.content
                if content:
                    yield content

        with st.chat_message(assistant_name, avatar=assistant_avatar):
            full_response = st.write_stream(generate_response)

        messages.append({"role": "assistant", "content": full_response})
        mgr.save_session(messages)      # 实时保存（首次保存会自动取首句当标题）

    except Exception as e:
        st.error(f"❌ 与 AI 通信发生错误: {e}")
