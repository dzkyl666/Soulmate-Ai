"""弹窗集合：把「要填一堆字段」的表单塞进 @st.dialog，主界面不被撑长。

套路统一：画字段 → 校验 → 写进服务 → st.rerun()。
弹窗之间不嵌套（Streamlit 同一时刻只能开一个对话框）。
校验失败用 st.error 就地显示，不打断对话框。
"""

from __future__ import annotations

import streamlit as st

from soulmate.core.exceptions import mask_secret
from soulmate.core.presets import (
    DEFAULT_SYSTEM_PROMPT,
    EMOJI_OPTIONS,
    PROVIDER_NAMES,
    PROVIDER_OPTIONS,
    PROVIDER_PRESETS,
)
from soulmate.services.container import ServiceContainer
from soulmate.ui import theme as ui_theme
from soulmate.ui.boundary import guard


def _svc() -> ServiceContainer:
    return st.session_state["__container"]


def reset_form_keys(prefix: str) -> None:
    """清掉某前缀下所有 widget 残留状态（避免重开弹窗看到上次内容）。"""
    for k in list(st.session_state.keys()):
        key = str(k)  # session_state 的键可能是 int，统一成 str 再判断
        if (key.startswith(prefix + "_") or key.startswith(prefix + "__")) and key != "__container":
            del st.session_state[key]


def _apply_preset(prefix: str) -> None:
    """厂商预设下拉的 on_change：自动填 Base URL 和首个模型名。"""
    preset_id = str(st.session_state.get(f"{prefix}_preset") or "")
    pp = PROVIDER_PRESETS.get(preset_id)
    if not pp:
        return
    st.session_state[f"{prefix}_base_url"] = pp["base_url"]
    st.session_state[f"{prefix}_model"] = pp["models"][0] if pp["models"] else ""


# ══════════════════════════════════════════════════════════
# 模型服务
# ══════════════════════════════════════════════════════════
def _provider_form(provider: dict, prefix: str) -> dict:
    # 有密文 key（已加密落盘）时回显掩码，编辑时默认不覆盖
    has_stored_key = bool(provider.get("api_key"))

    cur_preset = provider.get("preset") or "siliconflow"
    if cur_preset not in PROVIDER_PRESETS:
        cur_preset = "custom"
    p0 = PROVIDER_PRESETS[cur_preset]

    init = {
        f"{prefix}_preset": cur_preset,
        f"{prefix}_alias": provider.get("alias") or "",
        f"{prefix}_base_url": provider.get("base_url") or p0["base_url"],
        f"{prefix}_model": provider.get("model") or (p0["models"][0] if p0["models"] else ""),
    }
    for k, v in init.items():
        if k not in st.session_state:
            st.session_state[k] = v

    if has_stored_key:
        st.caption(f"当前已保存 Key（{mask_secret(provider.get('api_key', ''))}），不填则保留原 Key")

    st.selectbox(
        "厂商预设",
        options=PROVIDER_OPTIONS,
        format_func=lambda k: PROVIDER_NAMES[k],
        key=f"{prefix}_preset",
        on_change=lambda: _apply_preset(prefix),
        help="选一个帮你自动填好地址和模型名；选「自定义」则全部手填",
    )
    presets = PROVIDER_PRESETS[st.session_state[f"{prefix}_preset"]]

    col1, col2 = st.columns(2)
    with col1:
        alias = st.text_input("别名（自己认得就行）", key=f"{prefix}_alias", placeholder=f"例如：{presets['name']}-主力")
        base_url = st.text_input("Base URL", key=f"{prefix}_base_url")
    with col2:
        model = st.text_input("模型名称", key=f"{prefix}_model")
        api_key = st.text_input(
            "API Key（留空则读取同名环境变量）",
            type="password",
            key=f"{prefix}_key",
            value="",
            help="只保存在你服务器的密钥库里，落盘是加密的",
        )
    if presets.get("env_key"):
        st.caption(f"💡 Key 留空时会自动读取环境变量 `{presets['env_key']}`")

    return {
        "id": provider.get("id"),
        "preset": st.session_state[f"{prefix}_preset"],
        "alias": (alias or "").strip() or f"{presets['name']}-{(model or '').strip()}",
        "base_url": (base_url or "").strip(),
        "api_key": (api_key or "").strip() or (provider.get("api_key") or "" if has_stored_key else ""),
        "model": (model or "").strip(),
        "created_at": provider.get("created_at"),
    }


def _save_provider(prefix: str, provider: dict) -> None:
    svc = _svc()
    form = _provider_form(provider, prefix)
    # 边界统一捕 SoulmateError（见 ui/boundary.py）：
    # upsert 会写盘，磁盘满/只读时抛的是 StorageError ——
    # 只捕 ValidationError 的话这里会整页红框，与登录限流是同一类缺口。
    if guard(lambda: svc.providers.upsert(form), what="保存模型服务") is None:
        return
    st.rerun()


@st.dialog("添加模型服务", width="large")
def add_provider_dialog() -> None:
    st.caption("填入 OpenAI 兼容地址、Key 和模型名。Key 在服务器上加密保存。")
    _provider_form({}, "add_p")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("保存", type="primary", width="stretch", icon="💾", key="add_p_save"):
            _save_provider("add_p", {})
    with c2:
        if st.button("取消", width="stretch", icon="↩️", key="add_p_cancel"):
            st.rerun()


@st.dialog("编辑模型服务", width="large")
def edit_provider_dialog(provider: dict) -> None:
    _provider_form(provider, f"edit_p_{provider['id']}")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("保存", type="primary", width="stretch", icon="💾", key=f"e_{provider['id']}_save"):
            _save_provider(f"edit_p_{provider['id']}", provider)
    with c2:
        if st.button("取消", width="stretch", icon="↩️", key=f"e_{provider['id']}_cancel"):
            st.rerun()


@st.dialog("确认删除模型服务")
def delete_provider_dialog(provider: dict) -> None:
    svc = _svc()
    users = svc.providers.in_use(provider["id"], svc.companions.list_companions())
    name = provider.get("alias") or provider.get("model")
    st.write(f"确定要删除模型服务 **{name}** 吗？")
    if users:
        st.error(f"⚠️ 它还被 {len(users)} 个伴侣使用着：{'、'.join(users)}；请先改绑。")
        if st.button("知道了", width="stretch", key=f"dp_ok_{provider['id']}"):
            st.rerun()
        return
    c1, c2 = st.columns(2)
    with c1:
        if st.button("确定删除", type="primary", width="stretch", icon="✅", key=f"dp_c_{provider['id']}"):
            svc.providers.delete(provider["id"])
            st.rerun()
    with c2:
        if st.button("取消", width="stretch", icon="↩️", key=f"dp_x_{provider['id']}"):
            st.rerun()


# ══════════════════════════════════════════════════════════
# 伴侣
# ══════════════════════════════════════════════════════════
def _companion_form(companion: dict, prefix: str) -> dict:
    svc = _svc()
    provider_ids = [p.id for p in svc.providers.list_providers()]
    if not provider_ids:
        st.warning("还没有模型服务，请先添加一个。")
        return {"id": companion.get("id"), "name": "", "provider_id": "", "fallback_provider_id": ""}
    # 其余分支才有 provider 可选

    st.markdown("**① 绑定模型服务（可配备选做降级）**")

    def _provider_label(pid: str) -> str:
        p = svc.providers.get(pid)
        return f"「{p.label if p else pid}」"

    cur_pid = companion.get("provider_id")
    idx = provider_ids.index(cur_pid) if cur_pid in provider_ids else 0
    provider_id = st.selectbox(
        "主模型",
        options=provider_ids,
        index=idx,
        format_func=_provider_label,
        key=f"{prefix}_provider",
        label_visibility="collapsed",
    )
    fallback_options: list[str] = ["", *provider_ids]
    cur_fb = companion.get("fallback_provider_id")
    fallback_id = st.selectbox(
        "备选模型（主模型失败自动切换，可留空）",
        options=fallback_options,
        index=fallback_options.index(cur_fb) if cur_fb in fallback_options else 0,
        format_func=lambda i: "（不设置）" if not i else _provider_label(i),
        key=f"{prefix}_fallback",
    )
    if fallback_id not in provider_ids:
        fallback_id = ""

    st.markdown("**② 伴侣信息**")
    col1, col2 = st.columns(2)
    with col1:
        cur_avatar = companion.get("avatar") or "🧸"
        is_custom = cur_avatar not in EMOJI_OPTIONS
        st.selectbox("头像", options=EMOJI_OPTIONS, key=f"{prefix}_avatar", index=0 if is_custom else EMOJI_OPTIONS.index(cur_avatar))
        st.text_input("或粘贴任意 emoji（可选）", key=f"{prefix}_manual", value=cur_avatar if is_custom else "")
        name = st.text_input("名称", value=companion.get("name", ""), key=f"{prefix}_name", placeholder="例如：工作伴侣")
        purpose = st.text_input("用途", value=companion.get("purpose", ""), key=f"{prefix}_purpose", placeholder="例如：帮我梳理思路")
    with col2:
        system_prompt = st.text_area(
            "系统提示词",
            value=companion.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
            height=280,
            key=f"{prefix}_sp",
            help="留空用默认模板；{name}/{purpose} 会替换成你填的名字和用途",
        )
    avatar = (st.session_state.get(f"{prefix}_manual") or "").strip() or st.session_state.get(f"{prefix}_avatar", "🧸")
    return {
        "id": companion.get("id"),
        "name": (name or "").strip(),
        "purpose": (purpose or "").strip(),
        "avatar": avatar if avatar else "🧸",
        "provider_id": provider_id,
        "fallback_provider_id": fallback_id if fallback_id in provider_ids else "",
        "system_prompt": system_prompt,
        "created_at": companion.get("created_at"),
    }


@st.dialog("新建伴侣", width="large")
def add_companion_dialog() -> None:
    svc = _svc()
    if not svc.providers.list_providers():
        st.warning("还没有模型服务。请先在侧边栏「模型服务」里添加一个。")
        if st.button("知道了", width="stretch"):
            st.rerun()
        return
    form = _companion_form({}, "add_c")
    if st.button("创建伴侣", type="primary", width="stretch", icon="✨", key="add_c_save"):
        if not form.get("name"):
            st.error("名称不能为空")
        else:
            companion = svc.companions.upsert(form)
            st.session_state["_companion_id"] = companion.id
            st.session_state["_messages"] = []
            st.session_state["_loaded_key"] = None
            st.rerun()


@st.dialog("编辑伴侣", width="large")
def edit_companion_dialog(companion: dict) -> None:
    svc = _svc()
    form = _companion_form(companion, f"edit_c_{companion['id']}")
    if st.button("保存", type="primary", width="stretch", icon="💾", key=f"s_c_{companion['id']}"):
        if not form.get("name"):
            st.error("名称不能为空")
        else:
            svc.companions.upsert(form)
            st.rerun()


@st.dialog("确认删除伴侣")
def delete_companion_dialog(companion: dict) -> None:
    svc = _svc()
    n = len(svc.companions.list_metas(companion["id"]))
    st.write(f"确定要删除伴侣 **{companion.get('avatar')} {companion['name']}** 吗？")
    st.caption(f"⚠️ 该伴侣名下有 {n} 个会话，会一并删除且无法恢复。")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("确定删除", type="primary", width="stretch", icon="✅", key=f"dc_{companion['id']}"):
            svc.delete_companion_fully(companion["id"])
            st.session_state["_companion_id"] = None
            st.session_state["_messages"] = []
            st.session_state["_loaded_key"] = None
            st.rerun()
    with c2:
        if st.button("取消", width="stretch", icon="↩️", key=f"dx_{companion['id']}"):
            st.rerun()


# ══════════════════════════════════════════════════════════
# 会话
# ══════════════════════════════════════════════════════════
@st.dialog("确认删除会话")
def delete_session_dialog(session_id: str, title: str) -> None:
    svc = _svc()
    st.write(f"确定要删除会话 **{title}** 吗？")
    st.caption("⚠️ 删除后无法恢复。")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("确定删除", type="primary", width="stretch", icon="✅", key=f"ds_{session_id}"):
            svc.companions.delete_session(str(st.session_state.get("_companion_id") or ""), session_id)
            if st.session_state.get("_session_id") == session_id:
                st.session_state["_session_id"] = None
                st.session_state["_messages"] = []
                st.session_state["_loaded_key"] = None
            st.rerun()
    with c2:
        if st.button("取消", width="stretch", icon="↩️", key=f"sx_{session_id}"):
            st.rerun()


@st.dialog("重命名会话")
def rename_session_dialog(session_id: str, title: str) -> None:
    svc = _svc()
    new_title = st.text_input("新的会话名称", value=title, key=f"rn_{session_id}")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("保存", type="primary", width="stretch", icon="💾", key=f"rn_s_{session_id}"):
            if not (new_title or "").strip():
                st.error("名称不能为空")
            else:
                svc.companions.rename_session(str(st.session_state.get("_companion_id") or ""), session_id, new_title.strip())
                st.rerun()
    with c2:
        if st.button("取消", width="stretch", icon="↩️", key=f"rn_x_{session_id}"):
            st.rerun()


# ══════════════════════════════════════════════════════════
# 我的资料
# ══════════════════════════════════════════════════════════
@st.dialog("我的资料")
def edit_profile_dialog() -> None:
    svc = _svc()
    profile = svc.profile()
    cur_avatar = profile.avatar or "🐶"
    in_list = cur_avatar in EMOJI_OPTIONS
    avatar = st.selectbox("头像", options=EMOJI_OPTIONS, key="prof_avatar", index=0 if not in_list else EMOJI_OPTIONS.index(cur_avatar))
    manual = st.text_input("或粘贴任意 emoji（可选）", key="prof_manual", value="" if in_list else cur_avatar)
    nickname = st.text_input("昵称", value=profile.nickname, key="prof_nickname", placeholder="例如：永康")
    theme = st.selectbox("主题配色", options=list(ui_theme.THEMES), index=list(ui_theme.THEMES).index(profile.theme) if profile.theme in ui_theme.THEMES else 0, key="prof_theme")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("保存", type="primary", width="stretch", icon="💾", key="prof_save"):
            svc.profile_repo.update(
                nickname=nickname.strip(),
                avatar=(manual or "").strip() or avatar,
                theme=theme,
            )
            if ui_theme.current_theme_name() != theme:
                ui_theme.apply_theme(theme)
                st.rerun()
            st.rerun()
    with c2:
        if st.button("取消", width="stretch", icon="↩️", key="prof_cancel"):
            st.rerun()
