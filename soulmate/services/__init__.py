"""services 层：业务编排。只依赖 core / storage / llm，不依赖 streamlit。"""

from __future__ import annotations

from soulmate.services.container import ServiceContainer, get_container_singleton

__all__ = ["ServiceContainer", "get_container_singleton"]
