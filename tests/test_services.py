"""services 层测试：模型服务校验 / 伴侣会话 / 长期记忆 / 导出备份 / 用量 / 容器。"""

from __future__ import annotations

import json

import pytest

from soulmate.core.exceptions import ValidationError
from soulmate.core.models import ChatMessage, Provider
from soulmate.llm.service import LLMService
from soulmate.services.memory_service import MemoryService
from tests.fakes import FakeProvider


# ══════════════════════════════════════════════════════════
# 模型服务
# ══════════════════════════════════════════════════════════
class TestProviderService:
    def test_upsert_validates_and_fills_id(self, container):
        p = container.providers.upsert(
            {"preset": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": "sk-x"}
        )
        assert p.id and p.model == "gpt-4o"
        assert container.providers.get(p.id).api_key == "sk-x"

    def test_rejects_empty_url(self, container):
        with pytest.raises(ValidationError):
            container.providers.upsert({"base_url": "", "model": "m"})

    def test_rejects_empty_model(self, container):
        with pytest.raises(ValidationError):
            container.providers.upsert({"base_url": "https://x.com/v1", "model": ""})

    def test_rejects_private_url_by_default(self, container):
        with pytest.raises(ValidationError):
            container.providers.upsert({"base_url": "http://127.0.0.1:1234/v1", "model": "m"})

    def test_trailing_slash_normalised(self, container):
        p = container.providers.upsert({"base_url": "https://x.com/v1/", "model": "m"})
        assert p.base_url == "https://x.com/v1"

    def test_in_use_reports_companions(self, container):
        p = container.providers.upsert({"base_url": "https://x.com/v1", "model": "m"})
        container.companions.upsert({"name": "小团子", "provider_id": p.id, "avatar": "🧸"})
        assert container.providers.in_use(p.id, container.companions.list_companions()) == ["小团子"]

    def test_test_connection_failure_is_reported_not_raised(self, container, fake_registry):
        fake_registry.register("p1", FakeProvider("挂", fail=True))
        container.set_llm(LLMService(container.settings, registry=fake_registry))
        ok, _latency, msg = container.providers.test_connection(Provider(id="p1", model="m", base_url="https://x.com/v1", api_key="k"), container.llm)
        assert ok is False and msg

    def test_test_connection_reports_missing_key(self, container):
        ok, _latency, msg = container.providers.test_connection(Provider(id="p1", model="m", base_url="https://x.com/v1"), container.llm)
        assert ok is False and "Key" in msg

    def test_test_connection_success(self, container, fake_registry):
        fake_registry.register("p1", FakeProvider("好", reply="OK"))
        container.set_llm(LLMService(container.settings, registry=fake_registry))
        ok, latency, msg = container.providers.test_connection(Provider(id="p1", model="m", base_url="https://x.com/v1", api_key="k"), container.llm)
        assert ok is True and latency >= 0 and "OK" in msg


# ══════════════════════════════════════════════════════════
# 伴侣 / 会话
# ══════════════════════════════════════════════════════════
class TestCompanionService:
    def _mk_provider(self, container) -> Provider:
        return container.providers.upsert({"base_url": "https://x.com/v1", "model": "m", "api_key": "k"})

    def test_name_required(self, container):
        with pytest.raises(ValidationError):
            container.companions.upsert({"name": "  "})

    def test_default_prompt_template_is_filled(self, container):
        c = container.companions.upsert({"name": "小团子", "purpose": "陪伴", "avatar": "🧸"})
        assert "小团子" in c.system_prompt and "陪伴" in c.system_prompt
        assert "{name}" not in c.system_prompt

    def test_custom_prompt_placeholders_replaced(self, container):
        c = container.companions.upsert({"name": "阿蓝", "purpose": "工作", "avatar": "🧸", "system_prompt": "你是{name}，负责{purpose}"})
        assert c.system_prompt == "你是阿蓝，负责工作"

    def test_prompt_with_stray_braces_survives(self, container):
        c = container.companions.upsert({"name": "A", "avatar": "🧸", "system_prompt": "你是{name}，输出 JSON {a:1}"})
        assert c.system_prompt  # 不抛异常即可

    def test_fallback_provider_persisted(self, container):
        p1, p2 = self._mk_provider(container), self._mk_provider(container)
        c = container.companions.upsert({"name": "A", "avatar": "🧸", "provider_id": p1.id, "fallback_provider_id": p2.id})
        assert container.fallback_config(c).id == p2.id

    def test_fallback_config_none_when_unset(self, container):
        c = container.companions.upsert({"name": "A", "avatar": "🧸"})
        assert container.fallback_config(c) is None

    def test_auto_title_from_first_user_message(self, container):
        msgs = [
            ChatMessage(role="assistant", content="开场白"),
            ChatMessage(role="user", content="今天有点累"),
        ]
        assert container.companions.auto_title(msgs) == "今天有点累"

    def test_auto_title_truncates_long(self, container):
        title = container.companions.auto_title([ChatMessage(role="user", content="字" * 100)])
        assert len(title) == 21 and title.endswith("…")

    def test_auto_title_uses_first_user_msg_even_if_later_is_longer(self, container):
        msgs = [
            ChatMessage(role="user", content="短"),
            ChatMessage(role="user", content="后面这条很长" * 10),
        ]
        assert container.companions.auto_title(msgs) == "短"

    def test_auto_title_fallback_when_no_user_message(self, container):
        assert container.companions.auto_title([ChatMessage(role="assistant", content="hi")]) == "新会话"

    def test_save_messages_sets_auto_title(self, container):
        c = container.companions.upsert({"name": "A", "avatar": "🧸"})
        sid = container.companions.new_session_id()
        container.companions.save_messages(c.id, sid, [ChatMessage(role="user", content="你好呀")])
        assert container.companions.session_title(c.id, sid) == "你好呀"

    def test_empty_session_not_persisted(self, container):
        c = container.companions.upsert({"name": "A", "avatar": "🧸"})
        sid = container.companions.new_session_id()
        container.companions.save_messages(c.id, sid, [])
        assert container.companions.list_metas(c.id) == []

    def test_explicit_title_wins_and_is_not_overwritten(self, container):
        c = container.companions.upsert({"name": "A", "avatar": "🧸"})
        sid = container.companions.new_session_id()
        msgs = [ChatMessage(role="user", content="原始消息")]
        container.companions.save_messages(c.id, sid, msgs, title="AI起的名字", title_source="ai")
        container.companions.save_messages(c.id, sid, msgs)  # 再存一次，不传 title
        assert container.companions.session_title(c.id, sid) == "AI起的名字"

    def test_rename_session(self, container):
        c = container.companions.upsert({"name": "A", "avatar": "🧸"})
        sid = container.companions.new_session_id()
        container.companions.save_messages(c.id, sid, [ChatMessage(role="user", content="x")])
        container.companions.rename_session(c.id, sid, "新名字")
        assert container.companions.session_title(c.id, sid) == "新名字"
        with pytest.raises(ValidationError):
            container.companions.rename_session(c.id, sid, "   ")

    def test_new_session_ids_unique_within_same_second(self, container):
        ids = {container.companions.new_session_id() for _ in range(50)}
        assert len(ids) == 50

    def test_delete_companion_cascades(self, container):
        c = container.companions.upsert({"name": "A", "avatar": "🧸"})
        sid = container.companions.new_session_id()
        container.companions.save_messages(c.id, sid, [ChatMessage(role="user", content="x")])
        container.memory_repo.add_facts(c.id, ["关于A"])
        container.delete_companion_fully(c.id)
        assert container.companions.get(c.id) is None
        assert container.companions.list_metas(c.id) == []
        assert container.memory_repo.list_facts(c.id) == []

    def test_build_system_prompt_includes_profile_and_memory(self, container):
        c = container.companions.upsert({"name": "小团子", "avatar": "🧸"})
        container.profile_repo.update(nickname="永康")
        prompt = container.companions.build_system_prompt(c, container.profile(), "\n\n【记忆块】养猫")
        assert "小团子" in prompt and "永康" in prompt and "养猫" in prompt

    def test_build_system_prompt_without_profile_or_memory(self, container):
        c = container.companions.upsert({"name": "小团子", "avatar": "🧸"})
        prompt = container.companions.build_system_prompt(c, container.profile(), "")
        assert "小团子" in prompt
        assert "正在和你聊天的人" not in prompt

    def test_generate_ai_title_strips_punctuation(self, container, fake_registry):
        fake_registry.register("p1", FakeProvider("T", reply="《工作复盘》。"))
        container.set_llm(LLMService(container.settings, registry=fake_registry))
        p = Provider(id="p1", model="m", base_url="https://x.com/v1", api_key="k")
        msgs = [ChatMessage(role="user", content="聊聊工作")]
        assert container.companions.generate_ai_title(p, None, msgs) == "工作复盘"


# ══════════════════════════════════════════════════════════
# 长期记忆
# ══════════════════════════════════════════════════════════
class TestMemoryService:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('["用户叫李永康", "用户养猫"]', ["用户叫李永康", "用户养猫"]),
            ('```json\n["用户叫李永康"]\n```', ["用户叫李永康"]),
            ('好的，结果如下：["用户叫李永康"] 希望有帮助', ["用户叫李永康"]),
            ("[]", []),
            ("没有值得记住的信息", []),
            ("", []),
            (None, []),
            ('{"name": "李永康"}', []),
            ('["用户叫李永康", "", "  ", "养猫"]', ["用户叫李永康", "养猫"]),
            ("[broken", []),
        ],
    )
    def test_parse_extraction_edges(self, raw, expected):
        assert MemoryService.parse_extraction(raw) == expected

    def test_prompt_block_empty_when_no_facts(self, container):
        assert container.memory.to_prompt_block([]) == ""

    def test_prompt_block_contains_text_and_limit(self, container):
        facts = container.memory_repo.list_facts("c1")
        container.memory_repo.add_facts("c1", [f"事实{i}" for i in range(40)])
        facts = container.memory_repo.list_facts("c1")
        block = container.memory.to_prompt_block(facts, limit=5)
        assert block.count("- ") == 5
        assert "事实39" in block and "事实0" not in block

    def test_remember_from_exchange_writes_facts(self, container, fake_registry):
        fake_registry.register("p1", FakeProvider("T", reply='["用户叫李永康", "用户养猫"]'))
        container.set_llm(LLMService(container.settings, registry=fake_registry))
        container.memory = MemoryService(container.memory_repo, container.llm, container.settings)
        p = Provider(id="p1", model="m", base_url="https://x.com/v1", api_key="k")
        n = container.memory.remember_from_exchange("c1", [ChatMessage(role="user", content="我叫李永康，养猫")], p)
        assert n == 2
        assert "用户叫李永康" in [f.text for f in container.memory.list_facts("c1")]

    def test_extraction_failure_is_silent(self, container, fake_registry):
        """记忆是锦上添花：模型挂了也必须静默返回 0，不能抛异常。"""
        fake_registry.register("p1", FakeProvider("挂", fail=True))
        container.set_llm(LLMService(container.settings, registry=fake_registry))
        container.memory = MemoryService(container.memory_repo, container.llm, container.settings)
        p = Provider(id="p1", model="m", base_url="https://x.com/v1", api_key="k")
        assert container.memory.remember_from_exchange("c1", [ChatMessage(role="user", content="hi")], p) == 0

    def test_no_provider_returns_zero(self, container):
        assert container.memory.remember_from_exchange("c1", [ChatMessage(role="user", content="hi")], None) == 0

    def test_forget_fact_and_all(self, container):
        container.memory_repo.add_facts("c1", ["a", "b"])
        container.memory.forget_fact("c1", container.memory.list_facts("c1")[0].id)
        assert [f.text for f in container.memory.list_facts("c1")] == ["b"]
        container.memory.forget_all("c1")
        assert container.memory.list_facts("c1") == []


# ══════════════════════════════════════════════════════════
# 导出 / 备份
# ══════════════════════════════════════════════════════════
class TestExportService:
    def _seed(self, container):
        c = container.companions.upsert({"name": "小团子", "avatar": "🐱"})
        sid = container.companions.new_session_id()
        container.companions.save_messages(
            c.id, sid,
            [ChatMessage(role="user", content="今天累"), ChatMessage(role="assistant", content="抱抱")],
        )
        return c, sid, container.session_repo.load(c.id, sid)

    def test_markdown_contains_roles_and_text(self, container):
        c, _sid, doc = self._seed(container)
        md = container.export.session_to_markdown(c, doc)
        assert "# " in md and "今天累" in md and "抱抱" in md and "小团子" in md

    def test_json_is_valid_and_complete(self, container):
        _c, _sid, doc = self._seed(container)
        data = json.loads(container.export.session_to_json(doc))
        assert data["session_id"] == doc.session_id
        assert len(data["messages"]) == 2

    def test_backup_creates_copy_and_rotates(self, container):
        self._seed(container)
        made = [container.export.backup_user_data(container.user_dir) for _ in range(4)]

        # 最新那份必须还在（返回值要能立刻用）
        assert made[-1].is_dir()
        assert (made[-1] / "providers.json").exists() or (made[-1] / "companions.json").exists()

        backups_root = container.settings.backup_dir(container.user_id)
        remaining = sorted(p for p in backups_root.iterdir() if p.is_dir())
        # backup_keep=2 → 只留最近 2 份（旧的被轮转删掉，属预期行为）
        assert len(remaining) == 2
        assert remaining[-1] == made[-1]

    def test_backups_do_not_collide_within_same_second(self, container):
        """同一秒内连点两次备份不能互相覆盖（时间戳必须带微秒）。"""
        self._seed(container)
        container.settings_backup_keep = 5
        a = container.export.backup_user_data(container.user_dir)
        b = container.export.backup_user_data(container.user_dir)
        assert a != b
        assert a.is_dir() and b.is_dir()

    def test_backup_does_not_nest_itself(self, container):
        self._seed(container)
        container.export.backup_user_data(container.user_dir)
        dest = container.export.backup_user_data(container.user_dir)
        assert not (dest / "backups").exists()


# ══════════════════════════════════════════════════════════
# 用量统计
# ══════════════════════════════════════════════════════════
class TestMetrics:
    def test_record_and_summary(self, container):
        container.metrics.record(calls=2, prompt_tokens=100, completion_tokens=50)
        s = container.metrics.summary()
        assert s["today"]["calls"] == 2
        assert s["total"]["prompt"] == 100
        assert s["days"] == 1

    def test_accumulates_across_calls(self, container):
        container.metrics.record(calls=1, prompt_tokens=10, completion_tokens=5)
        container.metrics.record(calls=1, prompt_tokens=20, completion_tokens=7, errors=1)
        s = container.metrics.summary()
        assert s["today"]["calls"] == 2
        assert s["today"]["errors"] == 1
        assert s["total"]["completion"] == 12

    def test_prunes_old_days(self, container):
        path = container.user_dir / "metrics" / "usage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"2000-01-01": {"calls": 99, "prompt": 1, "completion": 1, "errors": 0}, "2020-05-05": {"calls": 5, "prompt": 1, "completion": 1, "errors": 0}}),
            encoding="utf-8",
        )
        container.metrics.record(calls=1)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert "2000-01-01" not in saved and "2020-05-05" not in saved


# ══════════════════════════════════════════════════════════
# 容器 / 隔离
# ══════════════════════════════════════════════════════════
class TestContainer:
    def test_two_users_are_physically_isolated(self, settings):
        from soulmate.services.container import ServiceContainer

        a = ServiceContainer(settings, "alice")
        b = ServiceContainer(settings, "bob")
        a.companions.upsert({"name": "Alice的伴侣", "avatar": "🧸"})
        assert [c.name for c in a.companions.list_companions()] == ["Alice的伴侣"]
        assert b.companions.list_companions() == []
        assert a.user_dir != b.user_dir

    def test_provider_config_lookup(self, container):
        p = container.providers.upsert({"base_url": "https://x.com/v1", "model": "m"})
        assert container.provider_config(p.id).model == "m"
        assert container.provider_config("不存在") is None

    def test_get_container_singleton_caches(self, settings):
        from soulmate.services.container import get_container_singleton

        store: dict = {}
        c1 = get_container_singleton(store, settings, "alice")
        c2 = get_container_singleton(store, settings, "alice")
        c3 = get_container_singleton(store, settings, "bob")
        assert c1 is c2 and c1 is not c3


# ══════════════════════════════════════════════════════════
# 诊断
# ══════════════════════════════════════════════════════════
class TestDiagnostics:
    def test_collect_has_expected_shape(self, container, settings):
        from soulmate.services.diagnostics import collect

        info = collect(settings, users_count=1, providers_count=2, user_id="tester")
        assert info["environments"]["env"] == "test"
        assert info["versions"]["soulmate"]
        assert info["data"]["current_user"] == "tester"
        assert info["secrets"]["app_secret_set"] is True
        assert info["security"]["allow_private_base_url"] is False

    def test_production_problems_surface(self, tmp_path):
        from soulmate.core.settings import Settings as S
        from soulmate.services.diagnostics import collect

        prod = S(env="production", app_secret="short", data_dir=tmp_path / "d", allow_signup=True)
        info = collect(prod)
        assert info["startup_problems"]
