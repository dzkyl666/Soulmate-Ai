"""长期记忆仓储：每个伴侣一个文件，存「关于用户的稳定事实」。

布局：`<用户>/memory/<伴侣id>.json`，内容为 `list[MemoryFact]`。

【去重】模型每轮对话都会抽一次，很容易反复抽出同一件事 —— 不查重就会记一百遍。
【上限】`max_facts` 到顶后淘汰最旧，防止文件无限膨胀（记忆只是辅助上下文，不值钱）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from soulmate.core.logging import get_logger
from soulmate.core.models import MemoryFact
from soulmate.storage.atomic import atomic_write_json, delete_file, read_json

log = get_logger("soulmate.storage.repos.memory")


class MemoryRepository:
    def __init__(self, root: Path) -> None:
        self._memory_root = Path(root) / "memory"

    def _path(self, companion_id: str) -> Path:
        return self._memory_root / f"{companion_id}.json"

    def list_facts(self, companion_id: str) -> list[MemoryFact]:
        records = read_json(self._path(companion_id), []) or []
        out: list[MemoryFact] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            try:
                out.append(MemoryFact.model_validate(r))
            except Exception:
                log.exception("记忆条目解析失败: %s", r)
        return out

    def add_facts(self, companion_id: str, texts: list[str], *, session_id: str = "", max_facts: int = 200) -> int:
        """追加若干条记忆，返回真正新增条数。自动去重 + 超限裁掉最旧。"""
        existing = self.list_facts(companion_id)
        seen = {f.text for f in existing}
        added = 0
        for t in texts or []:
            t = (t or "").strip()
            if not t or t in seen:
                continue
            existing.append(
                MemoryFact(
                    id=uuid.uuid4().hex[:8],
                    text=t,
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    session_id=session_id,
                )
            )
            seen.add(t)
            added += 1
        if added:
            # 超限淘汰最旧的（保持注入顺序 = 记录顺序）
            if max_facts > 0 and len(existing) > max_facts:
                existing = existing[-max_facts:]
            atomic_write_json(self._path(companion_id), [f.model_dump() for f in existing])
        return added

    def remove_fact(self, companion_id: str, fact_id: str) -> None:
        facts = [f for f in self.list_facts(companion_id) if f.id != fact_id]
        atomic_write_json(self._path(companion_id), [f.model_dump() for f in facts])

    def clear(self, companion_id: str) -> None:
        delete_file(self._path(companion_id))