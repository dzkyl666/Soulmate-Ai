"""storage 层测试：原子写 / 缓存 / 加密仓储 / 会话 / 记忆 / 资料。"""

from __future__ import annotations

import json

import pytest

from soulmate.core.models import ChatMessage, Companion, MemoryFact, Provider, SessionDoc, UserRecord
from soulmate.core.security import SecretBox
from soulmate.storage.atomic import (
    atomic_write_json,
    delete_dir,
    delete_file,
    list_json_stems,
    read_json,
)
from soulmate.storage.cache import MTimeCache
from soulmate.storage.repositories import (
    CompanionRepository,
    MemoryRepository,
    ProfileRepository,
    ProviderRepository,
    SessionRepository,
    UserRepository,
)

SECRET = "s" * 48


# ══════════════════════════════════════════════════════════
# 原子读写
# ══════════════════════════════════════════════════════════
class TestAtomicIO:
    def test_write_then_read_roundtrip(self, tmp_path):
        p = tmp_path / "sub" / "a.json"
        atomic_write_json(p, {"中文": "值", "n": 1})
        assert read_json(p) == {"中文": "值", "n": 1}
        # ensure_ascii=False：中文在文件里是可读的
        assert "中文" in p.read_text(encoding="utf-8")

    def test_missing_file_returns_default(self, tmp_path):
        assert read_json(tmp_path / "nope.json", default=[]) == []
        assert read_json(tmp_path / "nope.json") is None

    def test_no_tmp_left_behind(self, tmp_path):
        p = tmp_path / "a.json"
        atomic_write_json(p, [1, 2, 3])
        leftovers = [f.name for f in tmp_path.iterdir() if f.name.startswith(".tmp-")]
        assert leftovers == []

    def test_repeated_writes_keep_file_valid(self, tmp_path):
        """并发/连续写不能把文件写坏（旧版固定 .tmp 名的核心 bug）。"""
        p = tmp_path / "a.json"
        for i in range(30):
            atomic_write_json(p, {"i": i})
        assert read_json(p) == {"i": 29}

    def test_corrupt_file_is_preserved_and_default_returned(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{ this is not json", encoding="utf-8")
        assert read_json(p, default={"fallback": True}) == {"fallback": True}
        # 损坏文件被改名留证，而不是被静默覆盖或删除
        corrupt = list(tmp_path.glob("bad.json.corrupt-*"))
        assert len(corrupt) == 1
        assert "not json" in corrupt[0].read_text(encoding="utf-8")

    def test_delete_file_and_list_stems(self, tmp_path):
        atomic_write_json(tmp_path / "x.json", {})
        atomic_write_json(tmp_path / "y.json", {})
        (tmp_path / "ignore.txt").write_text("z", encoding="utf-8")
        assert sorted(list_json_stems(tmp_path)) == ["x", "y"]
        assert delete_file(tmp_path / "x.json") is True
        assert delete_file(tmp_path / "x.json") is True  # missing 也返回 True
        assert list_json_stems(tmp_path) == ["y"]

    def test_delete_dir_is_recursive_and_forgiving(self, tmp_path):
        d = tmp_path / "d"
        atomic_write_json(d / "deep" / "a.json", {})
        delete_dir(d)
        assert not d.exists()
        delete_dir(d)  # 再删一次不报错


# ══════════════════════════════════════════════════════════
# mtime 缓存
# ══════════════════════════════════════════════════════════
class TestMTimeCache:
    def test_caches_when_file_unchanged(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text('{"v": 1}', encoding="utf-8")
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return json.loads(p.read_text(encoding="utf-8"))

        cache = MTimeCache()
        assert cache.get(str(p), loader) == {"v": 1}
        assert cache.get(str(p), loader) == {"v": 1}
        assert calls["n"] == 1  # 第二次没读盘

    def test_invalidates_when_file_changes(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text('{"v": 1}', encoding="utf-8")
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return json.loads(p.read_text(encoding="utf-8"))

        cache = MTimeCache()
        cache.get(str(p), loader)
        p.write_text('{"v": 22}', encoding="utf-8")  # size 变了
        assert cache.get(str(p), loader) == {"v": 22}
        assert calls["n"] == 2

    def test_explicit_invalidate(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}", encoding="utf-8")
        cache = MTimeCache()
        cache.get(str(p), lambda: "first")
        cache.invalidate(str(p))
        assert cache.get(str(p), lambda: "second") == "second"

    def test_missing_file_calls_loader(self, tmp_path):
        cache = MTimeCache()
        assert cache.get(str(tmp_path / "none.json"), lambda: "default") == "default"


# ══════════════════════════════════════════════════════════
# Provider 仓储（加密落盘）
# ══════════════════════════════════════════════════════════
class TestProviderRepository:
    def test_api_key_never_stored_in_plaintext(self, tmp_path):
        repo = ProviderRepository(tmp_path, SecretBox(SECRET))
        repo.upsert(Provider(id="p1", model="gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-super-secret"))
        raw = (tmp_path / "providers.json").read_text(encoding="utf-8")
        assert "sk-super-secret" not in raw
        assert "api_key_enc" in raw
        assert "api_key" not in json.loads(raw)[0]

    def test_read_decrypts_back(self, tmp_path):
        repo = ProviderRepository(tmp_path, SecretBox(SECRET))
        repo.upsert(Provider(id="p1", model="m", base_url="https://x.com/v1", api_key="sk-abc"))
        assert repo.get("p1").api_key == "sk-abc"

    def test_legacy_plaintext_is_readable(self, tmp_path):
        """v1 数据里是明文 api_key —— 必须还能读出来（平滑升级）。"""
        (tmp_path / "providers.json").write_text(
            json.dumps([{"id": "old", "model": "m", "base_url": "https://x.com/v1", "api_key": "sk-legacy"}], ensure_ascii=False),
            encoding="utf-8",
        )
        repo = ProviderRepository(tmp_path, SecretBox(SECRET))
        assert repo.get("old").api_key == "sk-legacy"

    def test_legacy_plaintext_is_encrypted_on_next_save(self, tmp_path):
        path = tmp_path / "providers.json"
        path.write_text(
            json.dumps([{"id": "old", "model": "m", "base_url": "https://x.com/v1", "api_key": "sk-legacy"}], ensure_ascii=False),
            encoding="utf-8",
        )
        repo = ProviderRepository(tmp_path, SecretBox(SECRET))
        repo.upsert(repo.get("old"))  # 原样存回
        raw = path.read_text(encoding="utf-8")
        assert "sk-legacy" not in raw

    def test_key_from_other_secret_degrades_to_empty_not_crash(self, tmp_path):
        ProviderRepository(tmp_path, SecretBox(SECRET)).upsert(
            Provider(id="p1", model="m", base_url="https://x.com/v1", api_key="sk-abc")
        )
        repo2 = ProviderRepository(tmp_path, SecretBox("z" * 48))
        assert repo2.get("p1").api_key == ""  # 解不开就置空，不崩
        assert repo2.get("p1").model == "m"

    def test_upsert_updates_in_place_and_delete(self, tmp_path):
        repo = ProviderRepository(tmp_path, SecretBox(SECRET))
        repo.upsert(Provider(id="p1", model="a", base_url="https://x.com/v1", api_key="k1"))
        repo.upsert(Provider(id="p1", model="b", base_url="https://x.com/v1", api_key="k2"))
        assert len(repo.list()) == 1
        assert repo.get("p1").model == "b"
        assert repo.get("p1").api_key == "k2"
        repo.delete("p1")
        assert repo.list() == []


# ══════════════════════════════════════════════════════════
# 伴侣 / 会话
# ══════════════════════════════════════════════════════════
class TestCompanionRepository:
    def test_crud(self, tmp_path):
        repo = CompanionRepository(tmp_path)
        repo.upsert(Companion(id="c1", name="小团子", provider_id="p1", fallback_provider_id="p2"))
        got = repo.get("c1")
        assert got.name == "小团子" and got.fallback_provider_id == "p2"
        assert repo.list()[0].id == "c1"
        repo.delete("c1")
        assert repo.list() == []

    def test_ignores_unparsable_records(self, tmp_path):
        (tmp_path / "companions.json").write_text(
            json.dumps([{"id": "ok", "name": "好"}, "垃圾字符串", {"name": "没有id"}], ensure_ascii=False),
            encoding="utf-8",
        )
        repo = CompanionRepository(tmp_path)
        assert [c.name for c in repo.list()] == ["好", "没有id"]


class TestSessionRepository:
    def _doc(self, sid, texts):
        return SessionDoc(
            session_id=sid,
            title=texts[0] if texts else "",
            messages=[ChatMessage(role="user", content=t) for t in texts],
        )

    def test_save_load_list(self, tmp_path):
        repo = SessionRepository(tmp_path)
        repo.save("c1", self._doc("s1", ["你好"]))
        repo.save("c1", self._doc("s2", ["再来"]))
        loaded = repo.load("c1", "s1")
        assert loaded.messages[0].content == "你好"
        metas = repo.list_meta("c1")
        assert [m.session_id for m in metas] == ["s2", "s1"]  # 倒序：新的在前
        assert metas[0].count == 1

    def test_empty_sessions_are_filtered_out(self, tmp_path):
        repo = SessionRepository(tmp_path)
        repo.save("c1", SessionDoc(session_id="empty", messages=[]))
        assert repo.list_meta("c1") == []

    def test_write_invalidates_meta_cache(self, tmp_path):
        repo = SessionRepository(tmp_path)
        repo.save("c1", self._doc("s1", ["a"]))
        assert len(repo.list_meta("c1")) == 1
        repo.save("c1", self._doc("s2", ["b"]))
        assert len(repo.list_meta("c1")) == 2  # 不需要等 mtime 变化

    def test_delete_removes_and_invalidates(self, tmp_path):
        repo = SessionRepository(tmp_path)
        repo.save("c1", self._doc("s1", ["a"]))
        repo.delete("c1", "s1")
        assert repo.list_meta("c1") == []
        assert repo.load("c1", "s1") is None

    def test_companions_are_isolated(self, tmp_path):
        repo = SessionRepository(tmp_path)
        repo.save("c1", self._doc("s1", ["属于c1"]))
        repo.save("c2", self._doc("s1", ["属于c2"]))
        assert repo.load("c1", "s1").messages[0].content == "属于c1"
        assert repo.load("c2", "s1").messages[0].content == "属于c2"

    def test_missing_dir_returns_empty(self, tmp_path):
        assert SessionRepository(tmp_path).list_meta("不存在") == []


# ══════════════════════════════════════════════════════════
# 长期记忆
# ══════════════════════════════════════════════════════════
class TestMemoryRepository:
    def test_dedup_and_count(self, tmp_path):
        repo = MemoryRepository(tmp_path)
        n = repo.add_facts("c1", ["用户叫李永康", "用户叫李永康", "养猫", "  ", ""], session_id="s1")
        assert n == 2
        assert [f.text for f in repo.list_facts("c1")] == ["用户叫李永康", "养猫"]

    def test_dedup_across_calls(self, tmp_path):
        repo = MemoryRepository(tmp_path)
        repo.add_facts("c1", ["用户叫李永康"])
        assert repo.add_facts("c1", ["用户叫李永康"]) == 0
        assert len(repo.list_facts("c1")) == 1

    def test_fact_has_id_time_session(self, tmp_path):
        repo = MemoryRepository(tmp_path)
        repo.add_facts("c1", ["某事实"], session_id="sess-9")
        f = repo.list_facts("c1")[0]
        assert f.id and f.created_at and f.session_id == "sess-9"

    def test_cap_evicts_oldest(self, tmp_path):
        repo = MemoryRepository(tmp_path)
        repo.add_facts("c1", [f"事实{i}" for i in range(10)], max_facts=3)
        assert [f.text for f in repo.list_facts("c1")] == ["事实7", "事实8", "事实9"]

    def test_remove_and_clear(self, tmp_path):
        repo = MemoryRepository(tmp_path)
        repo.add_facts("c1", ["a", "b"])
        repo.remove_fact("c1", repo.list_facts("c1")[0].id)
        assert [f.text for f in repo.list_facts("c1")] == ["b"]
        repo.clear("c1")
        assert repo.list_facts("c1") == []
        assert not (tmp_path / "memory" / "c1.json").exists()

    def test_companions_isolated(self, tmp_path):
        repo = MemoryRepository(tmp_path)
        repo.add_facts("c1", ["关于c1"])
        repo.add_facts("c2", ["关于c2"])
        assert [f.text for f in repo.list_facts("c1")] == ["关于c1"]


# ══════════════════════════════════════════════════════════
# 资料 / 用户
# ══════════════════════════════════════════════════════════
class TestProfileRepository:
    def test_defaults_when_missing(self, tmp_path):
        p = ProfileRepository(tmp_path).get()
        assert p.nickname == "" and p.theme == "月白"

    def test_merge_update_keeps_other_fields(self, tmp_path):
        repo = ProfileRepository(tmp_path)
        repo.update(nickname="永康")
        repo.update(theme="玄夜")
        p = repo.get()
        assert p.nickname == "永康" and p.theme == "玄夜"

    def test_corrupt_profile_falls_back_to_default(self, tmp_path):
        (tmp_path / "profile.json").write_text("{broken", encoding="utf-8")
        assert ProfileRepository(tmp_path).get().nickname == ""


class TestUserRepository:
    def test_crud_and_case_insensitive_lookup(self, tmp_path):
        repo = UserRepository(tmp_path)
        repo.upsert(UserRecord(username="alice", password_hash="h", role="admin"))
        assert repo.exists("ALICE")
        assert repo.get("Alice").role == "admin"
        repo.upsert(UserRecord(username="alice", password_hash="h2", role="user"))
        assert len(repo.list()) == 1 and repo.get("alice").password_hash == "h2"
        repo.delete("alice")
        assert repo.list() == []