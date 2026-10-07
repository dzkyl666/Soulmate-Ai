"""资料仓储：单个用户的「我的资料」（昵称 / 头像 / 主题偏好）。

所有字段带默认值；`update(**fields)` 合并式写入，未来加新字段不会丢旧数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from soulmate.core.models import Profile
from soulmate.storage.atomic import atomic_write_json, read_json


class ProfileRepository:
    def __init__(self, root: Path) -> None:
        self._path = Path(root) / "profile.json"

    def get(self) -> Profile:
        data = read_json(self._path, {}) or {}
        try:
            return Profile.model_validate(data)
        except Exception:
            return Profile()

    def update(self, **fields: Any) -> Profile:
        profile = self.get()
        updated = profile.model_copy(update={k: v for k, v in fields.items() if v is not None})
        atomic_write_json(self._path, updated.model_dump())
        return updated
