"""ServiceContainer：把「一个用户全部仓储 + 服务」组装成一个大对象。

【为什么要有它】
- services 之间需要互相协作（companion 删伴侣要同时清会话和记忆、
  拼 system_prompt 要先读 profile、AI 起名要调 llm…）；
- 每个登录用户的数据目录不同 → 每个用户一个实例；
- UI 层只需要拿「一个容器」就能干所有事，不需要自己拼装一堆依赖。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from soulmate.core.models import Companion, Provider
from soulmate.core.settings import Settings
from soulmate.llm.service import LLMService
from soulmate.services.companion_service import CompanionService
from soulmate.services.export_service import ExportService
from soulmate.services.memory_service import MemoryService
from soulmate.services.metrics import UsageMetrics
from soulmate.services.provider_service import ProviderService
from soulmate.storage.repositories import (
    CompanionRepository,
    MemoryRepository,
    ProfileRepository,
    ProviderRepository,
    SessionRepository,
    UserRepository,
)
from soulmate.storage.secrets import get_cipher


class ServiceContainer:
    """单用户的服务集合。构造它不联网、不写盘（除了惰性加密件）。"""

    def __init__(self, settings: Settings, user_id: str) -> None:
        self.settings = settings
        self.user_id = user_id
        self.user_dir: Path = settings.user_data_dir(user_id)

        cipher = get_cipher(settings.app_secret)

        # ── 仓储 ──
        self.user_repo = UserRepository(settings.data_root())
        self.provider_repo = ProviderRepository(self.user_dir, cipher)
        self.companion_repo = CompanionRepository(self.user_dir)
        self.session_repo = SessionRepository(self.user_dir)
        self.memory_repo = MemoryRepository(self.user_dir)
        self.profile_repo = ProfileRepository(self.user_dir)

        # ── 底层层 ―
        self.llm = LLMService(settings)
        self.metrics = UsageMetrics(self.user_dir)

        # ── 业务层 ──
        self.providers = ProviderService(self.provider_repo, settings)
        self.companions = CompanionService(self.companion_repo, self.session_repo, self.memory_repo, self.llm, settings)
        self.memory = MemoryService(self.memory_repo, self.llm, settings)
        self.export = ExportService(self.session_repo, settings)

    # ── 便捷方法（UI 常用，省得 UI 层拼装）──
    def provider_config(self, provider_id: str) -> Provider | None:
        return self.providers.get(provider_id)

    def fallback_config(self, companion: Companion) -> Provider | None:
        """伴侣配了备选模型就返回它的配置，否则 None。"""
        if not companion or not companion.fallback_provider_id:
            return None
        return self.providers.get(companion.fallback_provider_id)

    def profile(self):
        return self.profile_repo.get()

    def delete_companion_fully(self, companion_id: str) -> None:
        """删伴侣 = 删列表记录 + 会话目录 + 长期记忆，一次做完。"""
        self.companions.delete(companion_id)


def get_container_singleton(container: Any, settings: Settings, user_id: str) -> ServiceContainer:
    """在 Streamlit 的 session 里复用同一用户容器（避免每次 rerun 重建/重复读盘）。

    `container` 应为 `st.session_state`：第一次建，之后直接拿缓存实例。
    """
    key = f"__soulmate_container_{user_id}"
    if key not in container:
        container[key] = ServiceContainer(settings, user_id)
    return container[key]