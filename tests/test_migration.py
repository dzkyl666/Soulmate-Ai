"""迁移测试：v1 平铺数据 / v0 单层 session 数据 → v2 按用户目录。

重点验证：
1. 数据搬过去且内容不丢；
2. 幂等（跑两次结果一样，不重复搬、不覆盖已有）；
3. 「已有伴侣」时不重复造伴侣。
"""

from __future__ import annotations

from soulmate.core.models import ChatMessage
from soulmate.services.container import ServiceContainer
from soulmate.services.migration import migrate_legacy_flat, migrate_legacy_session_dir, run_all
from soulmate.storage.atomic import atomic_write_json, read_json


def _legacy_flat(data_root, **overrides):
    """在 data 根伪造一份 v1 平铺数据。"""
    data_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        data_root / "providers.json",
        [{"id": "p1", "preset": "openai", "alias": "老Key", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": "sk-legacy-123"}],
    )
    atomic_write_json(
        data_root / "companions.json",
        [{"id": "c1", "name": "小团子", "purpose": "陪伴", "avatar": "🐱", "system_prompt": "你叫 {name}", "provider_id": "p1"}],
    )
    atomic_write_json(data_root / "profile.json", {"nickname": "永康", "avatar": "🐶"})
    atomic_write_json(
        data_root / "sessions" / "c1" / "s-01.json",
        {"session_id": "s-01", "title": "老会话", "messages": [{"role": "user", "content": "以前说的话"}]},
    )
    atomic_write_json(data_root / "memory" / "c1.json", [{"id": "m1", "text": "用户叫李永康"}])
    for k, v in overrides.items():
        atomic_write_json(data_root / f"{k}.json", v)


class TestFlatMigration:
    def test_moves_everything_into_user_dir(self, settings):
        _legacy_flat(settings.data_root())
        container = ServiceContainer(settings, "alice")
        moved = migrate_legacy_flat(settings, "alice")

        assert set(moved) == {"providers.json", "companions.json", "profile.json", "sessions", "memory"}
        # 内容确实到了用户目录
        assert container.providers.get("p1").api_key == "sk-legacy-123"
        assert container.companions.get("c1").name == "小团子"
        assert container.profile().nickname == "永康"
        assert container.companions.load_messages("c1", "s-01")[0].content == "以前说的话"
        assert [f.text for f in container.memory_repo.list_facts("c1")] == ["用户叫李永康"]
        # 老位置已清空
        assert not (settings.data_root() / "providers.json").exists()

    def test_is_idempotent(self, settings):
        _legacy_flat(settings.data_root())
        first = migrate_legacy_flat(settings, "alice")
        second = migrate_legacy_flat(settings, "alice")
        assert first and second == []  # 第二次没东西可搬

    def test_does_not_overwrite_existing_user_data(self, settings):
        """用户目录里已有自己的 providers.json → 不能拿老的覆盖。"""
        container = ServiceContainer(settings, "alice")
        mine = container.providers.upsert({"base_url": "https://mine.com/v1", "model": "my-model", "api_key": "k"})
        _legacy_flat(settings.data_root())
        moved = migrate_legacy_flat(settings, "alice")
        assert "providers.json" not in moved
        assert [p.id for p in container.providers.list()] == [mine.id]

    def test_writes_marker(self, settings):
        _legacy_flat(settings.data_root())
        migrate_legacy_flat(settings, "alice")
        assert (settings.user_data_dir("alice") / ".migrated").exists()

    def test_no_legacy_data_is_a_noop(self, settings):
        assert migrate_legacy_flat(settings, "alice") == []

    def test_second_user_gets_nothing(self, settings):
        _legacy_flat(settings.data_root())
        migrate_legacy_flat(settings, "alice")
        assert migrate_legacy_flat(settings, "bob") == []


class TestLegacySessionMigration:
    def _seed_v0(self, legacy_dir):
        legacy_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            legacy_dir / "2026-09-10_19-48-47.json",
            {"nickname": "老街坊", "nature": "温柔体贴", "messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "在的"}]},
        )
        atomic_write_json(legacy_dir / "2026-09-11_11-29-32.json", {"nickname": "老街坊", "nature": "温柔体贴", "messages": [{"role": "user", "content": "第二段"}]})

    def test_creates_companion_and_sessions(self, settings):
        self._seed_v0(settings.legacy_session_path)
        container = ServiceContainer(settings, "alice")
        moved = migrate_legacy_session_dir(settings, container, legacy_dir=settings.legacy_session_path)

        assert moved == 2
        comps = container.companions.list()
        assert len(comps) == 1 and comps[0].name == "老街坊"
        metas = container.companions.list_metas(comps[0].id)
        assert len(metas) == 2
        assert container.companions.load_messages(comps[0].id, "2026-09-10_19-48-47")[0].content == "你好"
        # 老文件搬完即删
        assert list(settings.legacy_session_path.glob("*.json")) == []

    def test_skipped_when_companions_exist(self, settings):
        self._seed_v0(settings.legacy_session_path)
        container = ServiceContainer(settings, "alice")
        container.companions.upsert({"name": "已有的伴侣", "avatar": "🧸"})
        assert migrate_legacy_session_dir(settings, container, legacy_dir=settings.legacy_session_path) == 0
        assert [c.name for c in container.companions.list()] == ["已有的伴侣"]

    def test_no_dir_is_noop(self, settings):
        container = ServiceContainer(settings, "alice")
        assert migrate_legacy_session_dir(settings, container, legacy_dir=settings.legacy_session_path) == 0

    def test_ignores_empty_message_files(self, settings):
        legacy = settings.legacy_session_path
        legacy.mkdir(parents=True, exist_ok=True)
        atomic_write_json(legacy / "empty.json", {"nickname": "空", "messages": []})
        container = ServiceContainer(settings, "alice")
        assert migrate_legacy_session_dir(settings, container, legacy_dir=legacy) == 0


class TestRunAll:
    def test_run_all_handles_both_generations(self, settings):
        """同时存在 v0 与 v1 数据也不会互相打架。"""
        _legacy_flat(settings.data_root())
        container = ServiceContainer(settings, "alice")
        summary = run_all(settings, "alice", container)
        assert summary["flat_moved"]
        # v1 里已经有伴侣了 → v0 那套不再重复造伴侣
        assert summary["legacy_sessions"] == 0

    def test_run_all_is_safe_to_repeat(self, settings):
        _legacy_flat(settings.data_root())
        container = ServiceContainer(settings, "alice")
        run_all(settings, "alice", container)
        again = run_all(settings, "alice", container)
        assert again == {"flat_moved": [], "legacy_sessions": 0}

    def test_run_all_on_fresh_install(self, settings):
        container = ServiceContainer(settings, "alice")
        assert run_all(settings, "alice", container) == {"flat_moved": [], "legacy_sessions": 0}
        assert container.companions.list() == []


class TestMigrationPreservesData:
    def test_long_conversation_survives_intact(self, settings):
        msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}条"} for i in range(120)]
        settings.data_root().mkdir(parents=True, exist_ok=True)
        atomic_write_json(settings.data_root() / "companions.json", [{"id": "c1", "name": "长会话"}])
        atomic_write_json(settings.data_root() / "sessions" / "c1" / "s.json", {"session_id": "s", "title": "长", "messages": msgs})

        container = ServiceContainer(settings, "alice")
        migrate_legacy_flat(settings, "alice")
        loaded = container.companions.load_messages("c1", "s")
        assert len(loaded) == 120
        assert loaded[0].content == "第0条" and loaded[-1].content == "第119条"
        assert isinstance(loaded[0], ChatMessage)

    def test_unicode_content_survives(self, settings):
        settings.data_root().mkdir(parents=True, exist_ok=True)
        atomic_write_json(settings.data_root() / "profile.json", {"nickname": "永康🧸", "avatar": "🐶"})
        container = ServiceContainer(settings, "alice")
        migrate_legacy_flat(settings, "alice")
        assert container.profile().nickname == "永康🧸"
        raw = read_json(settings.user_data_dir("alice") / "profile.json")
        assert raw["nickname"] == "永康🧸"