"""老数据一键迁移：把 v1 版本（平铺 data/ + 更老的 session/）搬进新结构。

【为什么需要】
v1 的数据长这样：
    data/providers.json / companions.json / profile.json
    data/sessions/<伴侣id>/<会话id>.json
    data/memory/<伴侣id>.json
    （更老的版本还有 项目根/session/*.json 单层会话）

v2 的结构是：
    data/users/<用户名>/providers.json ...
所有迁移函数都是**幂等**的：目标已存在就不动，跑多少次结果都一样。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from soulmate.core.logging import get_logger
from soulmate.core.models import ChatMessage
from soulmate.core.presets import DEFAULT_SYSTEM_PROMPT
from soulmate.core.settings import PROJECT_ROOT, Settings
from soulmate.services.companion_service import CompanionService
from soulmate.services.container import ServiceContainer
from soulmate.storage.atomic import delete_file, read_json, atomic_write_json

log = get_logger("soulmate.services.migration")


def migrate_legacy_flat(settings: Settings, user_id: str) -> list[str]:
    """把项目根 data/ 下的 v1 平铺文件搬进该用户的目录。

    只搬「用户目录里还没有」的项；搬完在用户目录写 `.migrated` 标记。
    返回搬了什么（文件/目录名列表）。
    """
    data_root = settings.data_root()
    user_dir = settings.user_data_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    candidates = [
        ("providers.json", data_root / "providers.json", user_dir / "providers.json"),
        ("companions.json", data_root / "companions.json", user_dir / "companions.json"),
        ("profile.json", data_root / "profile.json", user_dir / "profile.json"),
        ("sessions", data_root / "sessions", user_dir / "sessions"),
        ("memory", data_root / "memory", user_dir / "memory"),
    ]
    for name, src, dst in candidates:
        if not src.exists():
            continue
        if dst.exists():
            continue  # 目标已有，不覆盖（幂等）
        try:
            shutil.move(str(src), str(dst))
            moved.append(name)
        except OSError:
            log.warning("迁移 %s 失败（跳过，避免挡后续）", src)

    if moved:
        (user_dir / ".migrated").write_text("\n".join(moved), encoding="utf-8")
    return moved


def migrate_legacy_session_dir(settings: Settings, container: ServiceContainer, legacy_dir: Path | None = None) -> int:
    """把更老的 `项目根/session/*.json`（单层会话）搬成「一个伴侣 + 会话」。

    只在「用户一个伴侣都还没有」时才跑（幂等锚点：有伴侣说明搬过了/用户已自建）。
    返回搬了几个会话。`legacy_dir` 默认取项目根下的 session/（也可注入，便于测试）。
    """
    legacy_dir = legacy_dir or (PROJECT_ROOT / "session")
    if not legacy_dir.is_dir():
        return 0
    files = sorted(p for p in legacy_dir.iterdir() if p.suffix == ".json")
    if not files:
        return 0
    if container.companions.list():
        return 0  # 已有伴侣 = 已迁移过（或用户自己建了），不打扰

    first = read_json(files[0], {}) or {}
    name = (first.get("nickname") or "我的伴侣").strip() or "我的伴侣"
    purpose = (first.get("nature") or "").strip()

    companion = container.companions.upsert({
        "name": name,
        "purpose": purpose,
        "avatar": "🧸",
        "system_prompt": DEFAULT_SYSTEM_PROMPT.format(name=name, purpose=purpose or "温柔体贴"),
        "provider_id": "",
    })

    moved = 0
    for p in files:
        data = read_json(p, {}) or {}
        msgs = [ChatMessage(role=m.get("role", "user"), content=str(m.get("content") or "")) for m in (data.get("messages") or [])]
        msgs = [m for m in msgs if m.content]
        if not msgs:
            continue
        sid = p.stem
        container.companions.save_messages(
            companion.id,
            sid,
            msgs,
            title=container.companions.auto_title(msgs),
            title_source="auto",
        )
        moved += 1
        delete_file(p)

    return moved


def run_all(settings: Settings, user_id: str, container: ServiceContainer) -> dict[str, Any]:
    """用户首次登录时调用：整体迁移并返回摘要。幂等，可反复跑。"""
    summary: dict[str, Any] = {"flat_moved": [], "legacy_sessions": 0}
    summary["flat_moved"] = migrate_legacy_flat(settings, user_id)
    if not container.companions.list():
        summary["legacy_sessions"] = migrate_legacy_session_dir(settings, container)
    container.session_repo.clear()  # 迁完清缓存，让新数据立刻可读
    if summary["flat_moved"] or summary["legacy_sessions"]:
        log.info("用户 %s 迁移完成: %s", user_id, summary)
    return summary