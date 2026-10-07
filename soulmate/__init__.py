"""Soulmate AI —— 多伴侣 / 多模型 / 多用户的 AI 聊天应用。

本包按「依赖只能向下」严格分层，任何一层都不许反向 import：

    soulmate.core      零业务依赖：配置、日志、异常、领域模型、安全、校验
    soulmate.storage   持久化：原子写、缓存、加密、按用户隔离的仓储
    soulmate.llm       模型接入：Provider 抽象、重试、流式事件、降级链
    soulmate.services  业务编排：模型服务、伴侣会话、长期记忆、导出、诊断
    soulmate.auth      身份：本机账号 + OIDC 双后端
    soulmate.ui        表现：Streamlit 页面与组件（唯一允许 import streamlit 的地方之一）

为什么要这么严：分层一旦可以反向依赖，就必然退化成互相 import 的一团，
单测没法只测一层，换界面（Streamlit → FastAPI）也没法只换顶层。
"""

from __future__ import annotations

__version__ = "2.0.0"
__all__ = ["__version__"]
