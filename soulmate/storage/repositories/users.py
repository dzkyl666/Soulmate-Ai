"""用户账号仓储（全局一份，不在任何用户目录里）。

位置：`<data>/users.json`。一个用户 = 一条记录 + 一个数据目录 `<data>/users/<username>/`。
"""

from __future__ import annotations

from pathlib import Path

from soulmate.core.exceptions import StorageError
from soulmate.core.logging import get_logger
from soulmate.core.models import UserRecord
from soulmate.storage.atomic import atomic_write_json, read_json

log = get_logger("soulmate.storage.repos.users")


class UserRepository:
    def __init__(self, root: Path) -> None:
        """root = 数据根目录（users.json 就放在这里）。"""
        self._path = Path(root) / "users.json"

    def list(self) -> list[UserRecord]:
        records = read_json(self._path, []) or []
        out: list[UserRecord] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            try:
                out.append(UserRecord.model_validate(r))
            except Exception:
                log.exception("用户记录解析失败: %s", r.get("username"))
        return out

    def get(self, username: str) -> UserRecord | None:
        u = username.strip().lower()
        return next((x for x in self.list() if x.username == u), None)

    def exists(self, username: str) -> bool:
        return self.get(username) is not None

    def upsert(self, user: UserRecord) -> UserRecord:
        users = self.list()
        idx = next((i for i, x in enumerate(users) if x.username == user.username), None)
        if idx is None:
            users.append(user)
        else:
            users[idx] = user
        try:
            atomic_write_json(self._path, [u.model_dump() for u in users])
        except StorageError:
            raise
        return user

    def delete(self, username: str) -> None:
        users = [u for u in self.list() if u.username != username.strip().lower()]
        atomic_write_json(self._path, [u.model_dump() for u in users])
