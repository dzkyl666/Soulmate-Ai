"""伴侣仓储：伴侣列表的 CRUD，无加密需求，直接用基类。"""

from __future__ import annotations

from typing import Any

from soulmate.core.models import Companion
from soulmate.storage.repositories.base import ListRepository


class CompanionRepository(ListRepository):
    FILE = "companions.json"
    MODEL: type[Any] = Companion

    def get_by_name(self, name: str) -> Companion | None:
        return next((c for c in self.list() if c.name == name), None)
