"""
弹窗集合
========
对标参考项目 04-multibot 的 custom_pages/utils/dialogs.py：
把"要填一堆字段"的表单塞进 @st.dialog 弹窗，主界面就不会被表单撑长。

所有弹窗都是同一个套路：
    画字段 → 点「保存」→ 写进 manager → st.rerun() 让界面刷新
注意：弹窗之间不互相嵌套（Streamlit 同一时刻只能开一个），
所以「删除」都放到侧边栏的列表按钮上，各自独立开一个确认弹窗。
"""
import streamlit as st

from config import (
    EMOJI_OPTIONS,
    PROVIDER_PRESETS,
    PROVIDER_OPTIONS,
    PROVIDER_NAMES,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_AVATAR,
)


def _manager():
    return st.session_state["manager"]


def reset_form_keys(key_prefix):
    """清掉某个表单前缀下所有残留的 widget 状态。

    为什么需要：widget 的 key 一旦写进 session_state 就不会自己消失。
    如果不清，第二次打开「添加模型服务」弹窗时，会看到上次填过的内容，
    容易手滑又存一份重复的配置。
    所以"打开弹窗的那个按钮"在调用弹窗之前，先按前缀清一次。
    """
    for k in list(st.session_state.keys()):
        if k.startswith(key_prefix + "_"):
            del st.session_state[k]


def apply_provider_preset(key_prefix):
    """「厂商预设」下拉的 on_change 回调：把该厂商的 Base URL / 首个模型名填进表单。

    抽成独立函数是为了能被单独测试（弹窗里的闭包测不到）。
    """
    preset_id = st.session_state.get(f"{key_prefix}_preset")
    pp = PROVIDER_PRESETS.get(preset_id)
    if not pp:
        return
    st.session_state[f"{key_prefix}_base_url"] = pp["base_url"]
    st.session_state[f"{key_prefix}_model"] = pp["models"][0] if pp["models"] else ""


# ══════════════════════════════════════════════════
# 模型服务：新增 / 编辑 / 删除
# ══════════════════════════════════════════════════
def _provider_form(provider, key_prefix):
    """模型服务的字段表单（新增和编辑共用）。

    这里没用 st.form：因为"选预设 → 自动填好 Base URL / 模型名"需要立刻重跑。
    """
    cur_preset = provider.get("preset") or "siliconflow"
    if cur_preset not in PROVIDER_PRESETS:
        cur_preset = "custom"
    p0 = PROVIDER_PRESETS[cur_preset]

    # 首次进弹窗时把默认值塞进 session_state；之后交给 widget 自己维护，
    # 这样"切预设自动填"才能覆盖已有内容。
    init = {
        f"{key_prefix}_preset": cur_preset,
        f"{key_prefix}_alias": provider.get("alias", ""),
        f"{key_prefix}_base_url": provider.get("base_url") or p0["base_url"],
        f"{key_prefix}_model": provider.get("model") or (p0["models"][0] if p0["models"] else ""),
        f"{key_prefix}_key": provider.get("api_key", ""),
    }
    for k, v in init.items():
        if k not in st.session_state:
            st.session_state[k] = v

    def _apply_preset():
        apply_provider_preset(key_prefix)

    preset = st.selectbox(
        "厂商预设",
        options=PROVIDER_OPTIONS,
        format_func=lambda k: PROVIDER_NAMES[k],
        key=f"{key_prefix}_preset",
        on_change=_apply_preset,
        help="选一个帮你自动填好地址和常用模型名；选「自定义」则全部手填",
    )
    p = PROVIDER_PRESETS[preset]

    col1, col2 = st.columns(2)
    with col1:
        alias = st.text_input("别名（自己认得就行）", key=f"{key_prefix}_alias",
                              placeholder=f"例如：{p['name']}-主力")
        base_url = st.text_input("Base URL", key=f"{key_prefix}_base_url")
    with col2:
        model = st.text_input("模型名称", key=f"{key_prefix}_model")
        api_key = st.text_input("API Key", type="password", key=f"{key_prefix}_key",
                                help="留空则自动读取系统里同名的环境变量")

    if p["env_key"]:
        st.caption(f"💡 Key 留空时会自动读取环境变量 `{p['env_key']}`")

    return {
        "id": provider.get("id"),
        "preset": preset,
        "alias": (alias or "").strip() or f"{p['name']}-{(model or '').strip()}",
        "base_url": (base_url or "").strip(),
        "api_key": (api_key or "").strip(),
        "model": (model or "").strip(),
        "created_at": provider.get("created_at"),
    }


@st.dialog("添加模型服务", width="large")
def add_provider():
    st.caption("填入服务商的 OpenAI 兼容地址、Key 和模型名即可。Key 只存在你自己电脑上。")
    new_provider = _provider_form({}, key_prefix="add_p")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("保存", type="primary", width="stretch", icon="💾", key="add_p_save"):
            if not new_provider["base_url"] or not new_provider["model"]:
                st.error("Base URL 和模型名称不能为空")
            else:
                _manager().add_provider(new_provider)
                st.rerun()
    with col2:
        if st.button("取消", width="stretch", icon="↩️", key="add_p_cancel"):
            st.rerun()


@st.dialog("编辑模型服务", width="large")
def edit_provider(provider):
    edited = _provider_form(provider, key_prefix=f"edit_p_{provider['id']}")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("保存", type="primary", width="stretch", icon="💾", key="edit_p_save"):
            if not edited["base_url"] or not edited["model"]:
                st.error("Base URL 和模型名称不能为空")
            else:
                _manager().update_provider(edited)
                st.rerun()
    with col2:
        if st.button("取消", width="stretch", icon="↩️", key="edit_p_cancel"):
            st.rerun()


@st.dialog("确认删除模型服务")
def confirm_delete_provider(provider):
    mgr = _manager()
    users = mgr.provider_in_use(provider["id"])
    name = provider.get("alias") or provider.get("model")
    st.write(f"确定要删除模型服务 **{name}** 吗？")

    if users:
        st.error(f"⚠️ 它还被 {len(users)} 个伴侣使用着：{'、'.join(users)}；请先把这些伴侣改绑到别的模型。")
        if st.button("知道了", width="stretch", key="delp_ok"):
            st.rerun()
        return

    col1, col2 = st.columns(2)
    with col1:
        if st.button("确定删除", type="primary", width="stretch", icon="✅", key="delp_confirm"):
            mgr.delete_provider(provider["id"])
            st.rerun()
    with col2:
        if st.button("取消", width="stretch", icon="↩️", key="delp_cancel"):
            st.rerun()


# ══════════════════════════════════════════════════
# 使用者资料（「我」自己的昵称 + 头像，全局一份）
# ══════════════════════════════════════════════════
@st.dialog("我的资料")
def edit_profile():
    """设置「我」自己的头像和昵称。

    和「编辑伴侣」的关键区别：这份**不分伴侣** —— 改一次，
    所有伴侣、所有会话里显示的都是这一份。
    """
    mgr = _manager()
    cur_avatar = mgr.user_avatar()

    # 头像可能是"从 40 个里选的"，也可能是"手填的"。手填的那种要放回 manual 框，
    # 否则下次打开弹窗会静默回落到列表第一项，把手填的头像弄丢。
    in_list = cur_avatar in EMOJI_OPTIONS
    init = {
        "prof_nickname": mgr.profile.get("nickname", ""),
        "prof_avatar": cur_avatar if in_list else EMOJI_OPTIONS[0],
        "prof_manual": "" if in_list else cur_avatar,
    }
    for k, v in init.items():
        if k not in st.session_state:
            st.session_state[k] = v

    st.caption("这是「你」自己的头像和名字 —— 设一次就行，所有伴侣、所有会话里都用这一份。")

    col1, col2 = st.columns(2)
    with col1:
        avatar = st.selectbox("头像", options=EMOJI_OPTIONS, key="prof_avatar")
        manual = st.text_input("或粘贴任意 emoji（可选，填了就优先用它）",
                               key="prof_manual", placeholder="例如 🐯")
    with col2:
        nickname = st.text_input("昵称", key="prof_nickname", placeholder="例如：永康")
        st.caption("留空则显示默认的「我」")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("保存", type="primary", width="stretch", icon="💾", key="prof_save"):
            mgr.update_profile((nickname or "").strip(), (manual or "").strip() or avatar)
            st.rerun()
    with col_b:
        if st.button("取消", width="stretch", icon="↩️", key="prof_cancel"):
            st.rerun()


# ══════════════════════════════════════════════════
# 伴侣：新增 / 编辑 / 删除
# ══════════════════════════════════════════════════
def _finish_system_prompt(text, name, purpose):
    """把系统提示词里的 {name} / {purpose} 占位符换成真实值。

    留空时用默认模板生成；用户自己写的内容原样保留。
    """
    text = (text or "").strip()
    values = {"name": name, "purpose": purpose or "温柔体贴"}
    if not text:
        return DEFAULT_SYSTEM_PROMPT.format(**values)
    if "{name}" in text or "{purpose}" in text:
        try:
            return text.format(**values)
        except Exception:
            return text
    return text


def _companion_form(companion, key_prefix):
    """伴侣字段表单（新增和编辑共用）—— 第一步先选模型，第二步填人设"""
    mgr = _manager()
    provider_ids = [p["id"] for p in mgr.providers]

    st.markdown("**① 先给 TA 选一个模型服务**")
    cur_pid = companion.get("provider_id")
    pid_index = provider_ids.index(cur_pid) if cur_pid in provider_ids else 0
    provider_id = st.selectbox(
        "模型服务",
        options=provider_ids,
        index=pid_index,
        format_func=mgr.provider_label,
        key=f"{key_prefix}_provider",
        label_visibility="collapsed",
    )

    st.markdown("**② 再填写伴侣信息**")
    col1, col2 = st.columns(2)
    with col1:
        cur_avatar = companion.get("avatar") or DEFAULT_AVATAR
        avatar_index = EMOJI_OPTIONS.index(cur_avatar) if cur_avatar in EMOJI_OPTIONS else 0
        avatar = st.selectbox("头像", options=EMOJI_OPTIONS, index=avatar_index, key=f"{key_prefix}_avatar")
        manual = st.text_input("或粘贴任意 emoji（可选，填了就优先用它）",
                               key=f"{key_prefix}_manual",
                               placeholder="例如 🦖")
        name = st.text_input("名称", value=companion.get("name", ""), key=f"{key_prefix}_name",
                             placeholder="例如：工作伴侣")
        purpose = st.text_input("用途", value=companion.get("purpose", ""), key=f"{key_prefix}_purpose",
                                placeholder="例如：帮我梳理工作思路")
    with col2:
        system_prompt = st.text_area(
            "系统提示词",
            value=companion.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
            height=320,
            key=f"{key_prefix}_sp",
            help="留空会用默认模板；模板里的 {name} / {purpose} 保存时会自动替换成你填的名字和用途",
        )

    return {
        "id": companion.get("id"),
        "name": (name or "").strip(),
        "purpose": (purpose or "").strip(),
        "avatar": (manual or "").strip() or avatar,
        "provider_id": provider_id,
        "system_prompt": system_prompt,
        "created_at": companion.get("created_at"),
    }


@st.dialog("新建伴侣", width="large")
def add_companion():
    mgr = _manager()
    if not mgr.providers:
        st.warning("还没有模型服务。请先在侧边栏「模型服务」里添加一个，再回来创建伴侣。")
        return

    new_companion = _companion_form({}, key_prefix="add_c")

    if st.button("创建伴侣", type="primary", width="stretch", icon="✨", key="add_c_save"):
        if not new_companion["name"]:
            st.error("名称不能为空")
        else:
            new_companion["system_prompt"] = _finish_system_prompt(
                new_companion["system_prompt"], new_companion["name"], new_companion["purpose"])
            created = _manager().add_companion(new_companion)
            _manager().switch_companion(created["id"])
            st.session_state["messages"] = _manager().load_messages()
            st.rerun()


@st.dialog("编辑伴侣", width="large")
def edit_companion(companion):
    edited = _companion_form(companion, key_prefix=f"edit_c_{companion['id']}")

    if st.button("保存", type="primary", width="stretch", icon="💾", key="edit_c_save"):
        if not edited["name"]:
            st.error("名称不能为空")
        else:
            edited["system_prompt"] = _finish_system_prompt(
                edited["system_prompt"], edited["name"], edited["purpose"])
            _manager().update_companion(edited)
            st.rerun()


@st.dialog("给伴侣绑定模型服务")
def bind_provider(companion):
    """轻量版「编辑伴侣」：只挑模型服务，不碰人设。

    专给"伴侣没绑模型 → 主区被拦住"这条路径用。
    走完整版「编辑伴侣」要滚一屏表单，而这里真正缺的只有一个下拉框。
    """
    mgr = _manager()
    provider_ids = [p["id"] for p in mgr.providers]
    if not provider_ids:
        st.warning("还没有任何模型服务。请先去侧边栏「模型服务」里添加一个，再回来绑定。")
        if st.button("知道了", width="stretch", key="bind_none_ok"):
            st.rerun()
        return

    st.caption(f"给 **{companion.get('avatar', '')} {companion['name']}** 挑一个模型服务，"
               "保存后立刻就能聊天。")
    st.caption("（同一个模型服务可以被多个伴侣共用，不会互相影响）")

    cur_pid = companion.get("provider_id")
    pid_index = provider_ids.index(cur_pid) if cur_pid in provider_ids else 0
    pid = st.selectbox(
        "模型服务",
        options=provider_ids,
        index=pid_index,
        format_func=mgr.provider_label,
        key=f"bind_{companion['id']}_provider",
    )

    # 让用户确认一下选中的到底是哪条（地址 + 模型名）
    p = mgr.get_provider(pid)
    if p:
        st.caption(f"🔗 {p.get('base_url')}　｜　🧠 {p.get('model')}")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("保存并开始聊天", type="primary", width="stretch", icon="💾",
                     key=f"bind_{companion['id']}_save"):
            mgr.update_companion({**companion, "provider_id": pid})
            st.rerun()
    with col2:
        if st.button("取消", width="stretch", icon="↩️", key=f"bind_{companion['id']}_cancel"):
            st.rerun()


@st.dialog("确认删除伴侣")
def confirm_delete_companion(companion):
    mgr = _manager()
    n = mgr.count_sessions(companion["id"])
    st.write(f"确定要删除伴侣 **{companion.get('avatar', '')} {companion['name']}** 吗？")
    st.caption(f"⚠️ 该伴侣名下有 **{n}** 个会话，会一并删除，且无法恢复。")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("确定删除", type="primary", width="stretch", icon="✅", key="delc_confirm"):
            mgr.delete_companion(companion["id"])
            st.session_state["messages"] = []
            st.rerun()
    with col2:
        if st.button("取消", width="stretch", icon="↩️", key="delc_cancel"):
            st.rerun()


# ══════════════════════════════════════════════════
# 会话：删除 / 重命名
# ══════════════════════════════════════════════════
@st.dialog("确认删除会话")
def confirm_delete_session(session_id, title):
    mgr = _manager()
    st.write(f"确定要删除会话 **{title}** 吗？")
    st.caption("⚠️ 删除后无法恢复，该会话的聊天记录会一起消失。")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("确定删除", type="primary", width="stretch", icon="✅", key="dels_confirm"):
            mgr.delete_session(session_id)
            st.session_state["messages"] = mgr.load_messages()
            st.rerun()
    with col2:
        if st.button("取消", width="stretch", icon="↩️", key="dels_cancel"):
            st.rerun()


@st.dialog("重命名会话")
def rename_session(session_id, title):
    new_title = st.text_input("新的会话名称", value=title, key="rename_input")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("保存", type="primary", width="stretch", icon="💾", key="rename_save"):
            if not (new_title or "").strip():
                st.error("名称不能为空")
            else:
                _manager().rename_session(new_title.strip(), session_id=session_id)
                st.rerun()
    with col2:
        if st.button("取消", width="stretch", icon="↩️", key="rename_cancel"):
            st.rerun()
