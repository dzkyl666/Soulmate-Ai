"""列表型仓储的公共基类：一个 JSON 数组 ↔ 一堆 pydantic 模型。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from soulmate.core.logging import get_logger
from soulmate.storage.atomic import atomic_write_json, read_json

log = get_logger("soulmate.storage.repos.base")


class ListRepository:
    """单个 JSON 文件存一个列表的通用 CRUD。

    子类只需：
        FILE = "xxx.json"
        def _to_model(self, record: dict) -> Model  （可选，默认直接用 model 解析）
        def _to_record(self, model) -> dict          （可选，默认 model_dump）
        MODEL = 领域模型类
    """

    FILE: str = ""
    MODEL: type[Any] | None = None

    def __init__(self, root: Path) -> None:
        self._path = Path(root) / self.FILE

    # ── 可覆写的钩子 ──
    def _to_model(self, record: dict) -> Any:
        assert self.MODEL is not None, "子类必须设置 MODEL 或覆写 _to_model"
        return self.MODEL.model_validate(record)

    def _to_record(self, model: Any) -> dict:
        return model.model_dump()

    # ── 通用读写 ──
    def _load(self) -> list[Any]:
        records = read_json(self._path, []) or []
        out: list[Any] = []
        for r in records:
            if not isinstance(r, dict):
                log.warning("忽略非对象记录: %s", r)
                continue
            try:
                out.append(self._to_model(r))
            except Exception:
                log.exception("忽略无法解析的记录 %s", r.get("id", r))
        return out

    def _save(self, items: list[Any]) -> None:
        atomic_write_json(self._path, [self._to_record(i) for i in items])

    # ── 对外接口 ──
    def list(self) -> list[Any]:
        return self._load()

    def get(self, oid: str) -> Any | None:
        return next((o for o in self._load() if getattr(o, "id", "") == oid), None)

    def upsert(self, model: Any) -> Any:
        items = self._load()
        oid = getattr(model, "id", "")
        idx = next((i for i, o in enumerate(items) if getattr(o, "id", "") == oid), None)
        if idx is None:
            items.append(model)
        else:
            items[idx] = model
        self._save(items)
        return model

    def delete(self, oid: str) -> None:
        self._save([o for o in self._load() if getattr(o, "id", "") != oid])

    def path(self) -> Path:
        return self._path
