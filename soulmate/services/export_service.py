"""导出与备份：
- `session_to_markdown()` / `session_to_json()` —— 把会话导出成可带走/可分享的文件
- `backup_user_data()` —— 把某个用户的数据目录整体备份到 backups/，并轮转保留 N 份
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from soulmate.core.logging import get_logger
from soulmate.core.models import Companion, SessionDoc
from soulmate.core.settings import Settings
from soulmate.storage.repositories import SessionRepository

log = get_logger("soulmate.services.export")


class ExportService:
    def __init__(self, sessions_repo: SessionRepository, settings: Settings) -> None:
        self._sessions = sessions_repo
        self._settings = settings

    def session_to_markdown(self, companion: Companion, doc: SessionDoc) -> str:
        """把一次会话渲染成 Markdown（带角色与时间），可直接发给别人看。"""
        lines = [
            f"# {doc.title or '会话'}",
            "",
            f"- 伴侣：{companion.avatar} {companion.name}",
            f"- 会话 ID：`{doc.session_id}`",
            f"- 创建：{doc.created_at}",
            f"- 更新：{doc.updated_at}",
            "",
            "---",
            "",
        ]
        for m in doc.messages:
            who = "🧑 用户" if m.role == "user" else f"{companion.avatar} {companion.name}"
            lines.append(f"### {who}\n\n{m.content}\n")
        return "\n".join(lines)

    @staticmethod
    def session_to_json(doc: SessionDoc) -> str:
        return json.dumps(doc.model_dump(), ensure_ascii=False, indent=2)

    def backup_user_data(self, user_dir: Path) -> Path:
        """把用户数据目录整体拷到 `<用户>/backups/<时间戳>/`，然后轮转保留。

        返回备份目录路径。失败抛 StorageError 由上层兜住。
        """
        backups_root = self._settings.backup_dir(user_dir.name)
        # 注意：源目录里如果已经包含 backups/，要排除，避免自我嵌套。
        # 时间戳带微秒：否则同一秒内连点两次备份会落到同一目录名上互相覆盖，
        # 「保留最近 N 份」的轮转计数也会算错。
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        dest = backups_root / ts
        dest.parent.mkdir(parents=True, exist_ok=True)

        def _ignore(_d: str, names: list[str]) -> list[str]:
            return [n for n in names if n == "backups"]

        shutil.copytree(user_dir, dest, ignore=_ignore, dirs_exist_ok=True)

        keep = self._settings.backup_keep
        if keep > 0:
            existing = sorted(p for p in backups_root.iterdir() if p.is_dir())
            for stale in existing[:-keep]:
                shutil.rmtree(stale, ignore_errors=True)
                log.info("轮转删除旧备份: %s", stale)
        return dest
