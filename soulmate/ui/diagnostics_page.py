"""诊断页：版本 / 数据 / 安全 / 启动自检 / 模型连通性一键测试。

纯只读巡检（唯一会动的是「连通性测试」—— 那是用户主动点的、真发一次最小请求）。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import streamlit as st

from soulmate.core.settings import Settings
from soulmate.services.container import ServiceContainer
from soulmate.services.diagnostics import collect


def _pkg_version(name: str) -> str:
    """读已安装包版本。

    用 importlib.metadata 而不是 `import openai` —— 只为拿一个版本号而把
    openai 引入 UI 层是没必要的耦合（也会让「import openai 只在 llm 层」这条
    边界失守）。
    """
    try:
        return version(name)
    except PackageNotFoundError:  # pragma: no cover - 未安装时
        return "unknown"


def _library_versions() -> dict[str, str]:
    """第三方库版本由 UI 层收集后注入给 services（保持 services 不依赖第三方 UI 库）。"""
    return {
        "streamlit": st.__version__,
        "openai": _pkg_version("openai"),
    }


def render_diagnostics(svc: ServiceContainer, settings: Settings, user_id: str) -> None:
    st.subheader("🩺 系统诊断")

    info = collect(
        settings,
        users_count=len(svc.user_repo.list()) if svc.user_repo else 0,
        providers_count=len(svc.providers.list_providers()),
        user_id=user_id,
        extra_versions=_library_versions(),
    )

    st.markdown("##### 环境与版本")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("运行环境", info["environments"]["env"])
        st.metric("Python", info["versions"]["python"])
    with c2:
        st.metric("认证模式", info["environments"]["auth_mode"])
        st.metric("Streamlit", info["versions"]["streamlit"])
    with c3:
        st.metric("自助注册", "开" if info["environments"]["allow_signup"] else "关")
        st.metric("OpenAI SDK", info["versions"]["openai"])

    st.markdown("##### 数据")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("数据根目录", info["data"]["root"][:20] + "…" if len(info["data"]["root"]) > 20 else info["data"]["root"])
    with c2:
        st.metric("数据体积", info["data"]["size"])
        st.metric("用户数", info["data"]["users"])
    with c3:
        st.metric("我的模型服务数", info["data"]["current_providers"])
        st.metric("当前用户", user_id)

    st.markdown("##### 安全")
    c1, c2 = st.columns(2)
    with c1:
        st.metric("根密钥已设置", "✅" if info["secrets"]["app_secret_set"] else "❌")
        st.metric("允许内网模型地址", "是" if info["security"]["allow_private_base_url"] else "否（推荐）")
    with c2:
        st.metric("聊天限流", f"{info['security']['rate_limit_chat_per_minute']} 次/分钟")
        st.metric("生产自检问题", len(info["startup_problems"]))

    if info["startup_problems"]:
        st.error("发现生产配置问题（仅 production 环境会拦启动）：")
        for p in info["startup_problems"]:
            st.error(f" - {p}")

    # ── 模型连通性一键测试 ──
    st.markdown("##### 模型连通性测试")
    st.caption("会真的发一次最小请求（约几秒）。用于排查「为什么伴侣不回复」。")
    if not svc.providers.list_providers():
        st.info("还没有模型服务。")
    else:
        for p in svc.providers.list_providers():
            label = p.alias or p.model
            key = f"ping_{p.id}"
            if st.button(f"⚡ 测试 {label}", key=key):
                ok, latency, msg = svc.providers.test_connection(p, svc.llm, timeout=10.0)
                if ok:
                    st.success(f"✅ {msg}（{latency}s）")
                else:
                    st.error(f"❌ {msg}")
