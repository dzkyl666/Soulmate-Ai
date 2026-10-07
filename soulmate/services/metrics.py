"""用量统计：本地记账，按天聚合。

【记什么】每次模型调用的次数、token 消耗、失败次数。
【存在哪】`<用户>/metrics/usage.json`，只保留最近 30 天。
【目的】让用户能看见「今天聊了多少、花了多少 token」，为成本治理提供数据。
不承诺实时精确（进程内计数，崩溃可能丢最后一两笔），够日常参考。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from soulmate.storage.atomic import atomic_write_json, read_json

_RETENTION_DAYS = 30


class UsageMetrics:
    def __init__(self, root: Path) -> None:
        self._path = Path(root) / "metrics" / "usage.json"

    def _load(self) -> dict:
        data = read_json(self._path, {}) or {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save(self, data: dict) -> None:
        # 只保留最近 30 天
        keep_from = (date.today() - timedelta(days=_RETENTION_DAYS)).isoformat()
        data = {k: v for k, v in data.items() if k >= keep_from}
        atomic_write_json(self._path, data)

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    def record(self, *, calls: int = 0, prompt_tokens: int = 0, completion_tokens: int = 0, errors: int = 0) -> None:
        data = self._load()
        day = data.setdefault(self._today(), {"calls": 0, "prompt": 0, "completion": 0, "errors": 0})
        day["calls"] += calls
        day["prompt"] += prompt_tokens
        day["completion"] += completion_tokens
        day["errors"] += errors
        self._save(data)

    def record_result(self, text: str, *, model: str, prompt_tokens: int, completion_tokens: int, errors: int = 0) -> None:
        calls = 1 if text is not None else 0
        self.record(calls=calls, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, errors=errors)

    def summary(self) -> dict:
        """今日 + 全时段汇总，给诊断页用。"""
        data = self._load()
        today = data.get(self._today(), {})
        totals = {"calls": 0, "prompt": 0, "completion": 0, "errors": 0}
        for d in data.values():
            for k in totals:
                totals[k] += d.get(k, 0)
        return {
            "today": today,
            "total": totals,
            "days": len(data),
        }

    def to_json(self) -> str:
        return json.dumps(self._load(), ensure_ascii=False, indent=2)