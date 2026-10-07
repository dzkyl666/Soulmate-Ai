"""会话仓储：每个伴侣一个目录，目录里每个会话一个 JSON 文件。

布局：`<用户>/sessions/<伴侣id>/<会话id>.json`

【性能设计】
侧边栏的「会话历史」每次 rerun 都要列出来（标题 / 消息数）。老实现每次全量读盘。
这里用两层缓存：
1. `MTimeCache`：单文件按 (mtime, size) 复用，文件没变就不重新解析；
2. 目录列表缓存：会话目录的 mtime 变了（增删文件）才重扫目录。
任何写操作（save/delete/rename）都会显式 `invalidate`，保证即时可见。

【并发安全】
写走原子写（唯一临时文件 + os.replace），同一进程内多个 rerun 不会互相踩坏。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from soulmate.core.logging import get_logger
from soulmate.core.models import SessionDoc, SessionMeta
from soulmate.storage.atomic import atomic_write_json, delete_file, read_json
from soulmate.storage.cache import MTimeCache

log = get_logger("soulmate.storage.repos.sessions")


class SessionRepository:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._sessions_root = self._root / "sessions"
        self._file_cache = MTimeCache()
        # (companion_id, dir_mtime_ns) -> list[SessionMeta]
        self._meta_cache: dict[str, tuple[int, list[SessionMeta]]] = {}

    # ── 路径 ──
    def sessions_dir(self, companion_id: str) -> Path:
        return self._sessions_root / companion_id

    def _session_path(self, companion_id: str, session_id: str) -> Path:
        return self.sessions_dir(companion_id) / f"{session_id}.json"

    # ── 读写 ──
    def load(self, companion_id: str, session_id: str) -> SessionDoc | None:
        path = self._session_path(companion_id, session_id)

        def _load() -> Any:
            data = read_json(path, None)
            if not data:
                return None
            try:
                return SessionDoc.model_validate(data)
            except Exception:
                log.exception("会话解析失败，已跳过: %s", path)
                return None

        return self._file_cache.get(str(path), _load)

    def save(self, companion_id: str, doc: SessionDoc) -> None:
        path = self._session_path(companion_id, doc.session_id)
        atomic_write_json(path, doc.model_dump())
        self._file_cache.invalidate(str(path))          # 新内容立刻可见
        self.invalidate_dir(companion_id)               # 目录列表同步刷新

    def delete(self, companion_id: str, session_id: str) -> None:
        path = self._session_path(companion_id, session_id)
        self._file_cache.invalidate(str(path))
        delete_file(path)
        self.invalidate_dir(companion_id)

    # ── 目录列表 ──
    def list_meta(self, companion_id: str) -> list[SessionMeta]:
        """列出某伴侣全部会话元信息，按 session_id 倒序（时间新在前）。"""
        d = self.sessions_dir(companion_id)
        try:
            dir_mtime = os.stat(d).st_mtime_ns
        except OSError:
            return []
        cached = self._meta_cache.get(companion_id)
        if cached is not None and cached[0] == dir_mtime:
            return cached[1]

        metas: list[SessionMeta] = []
        if d.is_dir():
            for name in sorted(os.listdir(d)):
                if not name.endswith(".json"):
                    continue
                sid = name[:-5]
                doc = self.load(companion_id, sid)
                if doc is None or not doc.messages:
                    continue  # 空会话 / 损坏文件自动过滤
                metas.append(
                    SessionMeta(
                        session_id=doc.session_id,
                        title=doc.title or "新会话",
                        title_source=doc.title_source,
                        updated_at=doc.updated_at,
                        count=len(doc.messages),
                    )
                )
        metas.sort(key=lambda m: m.session_id, reverse=True)
        self._meta_cache[companion_id] = (dir_mtime, metas)
        return metas

    def invalidate_dir(self, companion_id: str) -> None:
        self._meta_cache.pop(companion_id, None)

    def clear(self) -> None:
        self._meta_cache.clear()
        self._file_cache.clear()