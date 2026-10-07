"""仓储层：把「JSON 文件」翻译成「领域模型」，向上层提供干净的 CRUD。"""

from __future__ import annotations

from soulmate.storage.repositories.base import ListRepository
from soulmate.storage.repositories.companions import CompanionRepository
from soulmate.storage.repositories.memory import MemoryRepository
from soulmate.storage.repositories.profile import ProfileRepository
from soulmate.storage.repositories.providers import ProviderRepository
from soulmate.storage.repositories.sessions import SessionRepository
from soulmate.storage.repositories.users import UserRepository

__all__ = [
    "CompanionRepository",
    "ListRepository",
    "MemoryRepository",
    "ProfileRepository",
    "ProviderRepository",
    "SessionRepository",
    "UserRepository",
]
