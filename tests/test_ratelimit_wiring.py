"""限流**接线**测试 —— 证明配额真的挂在调用链上，而不是模块躺在那里没人用。

【为什么单独一个文件，而不是并进 test_core.py】
上一轮的教训：`tests/test_core.py::TestRateLimiter` 只测了 `RateLimiter` 类本身（零件）。
所以「实现 + 配置 + 测试 + 文档四件套齐全，但生产代码零调用」这种**空转**
它完全测不出来 —— 50/50 全绿与限流失效是并存的。

本文件一律**走真实业务调用链**（UserStore / LLMService / MemoryService / ServiceContainer），
绝不 `new RateLimiter` 自己测自己。

【这些测试为什么"能证伪"】
关键在于：如果接线被摘掉，测试必须**真的失败**，而不是照样通过。
- 例：`test_container_meters_its_own_llm_per_user` 里，若容器没把 user_id 传下去，
  alice 和 bob 会共用 `chat:anonymous` 这一个桶 → bob 也会被锁 → 断言失败。
  即：这条测试专门针对「user_id 有没有真的注入下去」这个具体缺陷。
"""

from __future__ import annotations

import pytest

from soulmate.auth.user_store import UserStore
from soulmate.core.exceptions import InvalidCredentials, RateLimitError, RegistrationDisabled
from soulmate.core.models import ChatMessage, Provider
from soulmate.core.settings import Settings
from soulmate.llm.service import LLMService
from soulmate.llm.types import EndEvent, ErrorEvent
from soulmate.services.container import ServiceContainer
from soulmate.services.memory_service import MemoryService
from soulmate.storage.repositories import MemoryRepository, UserRepository
from tests.fakes import FakeProvider, FakeRegistry

GOOD_PW = "Passw0rd123"
MSG = [ChatMessage(role="user", content="hi")]


def _settings(
    tmp_path, *, chat: int = 3, extract: int = 3, login: int = 3, register: int = 3
) -> Settings:
    """各档配额都设成小值，方便「第 N+1 次必须被拒」。"""
    return Settings(
        env="test",
        app_secret="t" * 48,
        data_dir=tmp_path / "data",
        legacy_session_dir=tmp_path / "legacy-session",
        rate_limit_chat_per_minute=chat,
        rate_limit_extract_per_minute=extract,
        rate_limit_login_per_minute=login,
        rate_limit_register_per_minute=register,
        max_retries=0,
    )


def _provider(pid: str = "p1") -> Provider:
    return Provider(id=pid, model="m", base_url="https://x.com/v1", api_key="k")


def _user_store(settings: Settings) -> UserStore:
    return UserStore(UserRepository(settings.data_root()), settings)


def _memory_service(settings: Settings, user_id: str, registry: FakeRegistry) -> MemoryService:
    llm = LLMService(settings, user_id, registry=registry)
    return MemoryService(MemoryRepository(settings.user_data_dir(user_id)), llm, settings, user_id)


# ══════════════════════════════════════════════════════════
# 证明 1 / 三档之一：登录限流（真实调用链：UserStore.authenticate）
# ══════════════════════════════════════════════════════════
class TestLoginTierIsWired:
    def test_nth_plus_one_attempt_raises(self, tmp_path):
        """配置限额 N=3 → 第 4 次 authenticate() 必须抛 RateLimitError。"""
        settings = _settings(tmp_path, login=3)
        store = _user_store(settings)
        store.bootstrap("alice", GOOD_PW)

        for _ in range(3):  # 前 3 次放行
            assert store.authenticate("alice", GOOD_PW).username == "alice"

        with pytest.raises(RateLimitError) as ei:
            store.authenticate("alice", GOOD_PW)
        assert ei.value.retry_after > 0, "限流异常必须带 retry_after（界面要显示等多久）"

    def test_failed_attempts_are_also_counted(self, tmp_path):
        """爆破防护的关键：**失败**的尝试也要计数，否则限流对爆破毫无意义。"""
        settings = _settings(tmp_path, login=3)
        store = _user_store(settings)
        store.bootstrap("alice", GOOD_PW)

        for _ in range(3):
            with pytest.raises(InvalidCredentials):
                store.authenticate("alice", "wrong-password")

        # 密码明明是对的，但配额已被失败尝试耗尽 → 必须限流
        with pytest.raises(RateLimitError):
            store.authenticate("alice", GOOD_PW)

    def test_limit_follows_configured_value(self, tmp_path):
        """改配置要真的生效（防「配置项是死配置」）。"""
        settings = _settings(tmp_path, login=1)
        store = _user_store(settings)
        store.bootstrap("bob", GOOD_PW)
        store.authenticate("bob", GOOD_PW)  # 第 1 次用完
        with pytest.raises(RateLimitError):
            store.authenticate("bob", GOOD_PW)


# ══════════════════════════════════════════════════════════
# 证明 1b / 四档之四：开号限流（真实调用链：UserStore.register / bootstrap）
# ══════════════════════════════════════════════════════════
class TestRegisterTierIsWired:
    """开号档的三个关键性质：**会拦**、**用全局桶**、**不重复记账**。

    为什么全局桶这件事要专门测：如果实现成 `register:{username}`，
    下面 `test_key_is_global_not_per_username` 会失败 —— 换名字就不受限了，
    而「换名字」恰恰是刷号攻击的标准做法。这条测试就是钉这个语义的。
    """

    def test_register_is_rate_limited(self, tmp_path):
        """配置开号限额 N=3 → 第 4 次 register() 必须抛 RateLimitError。"""
        settings = _settings(tmp_path, register=3, login=99)
        store = _user_store(settings)
        # 第 1 次无用户 → 走 bootstrap 分支；后两次走正常注册
        for i in range(3):
            store.register(f"user{i}", GOOD_PW)
        with pytest.raises(RateLimitError):
            store.register("one-more", GOOD_PW)

    def test_bootstrap_itself_is_metered(self, tmp_path):
        """首启创建管理员也记账 ——「谁先到谁是 admin」的窗口不该完全不设速率。"""
        settings = _settings(tmp_path, register=1)
        store = _user_store(settings)
        store.bootstrap("first", GOOD_PW)  # 第 1 次放行
        with pytest.raises(RateLimitError):
            store.bootstrap("second", GOOD_PW)  # 第 2 次被拒

    def test_register_via_bootstrap_charges_only_once(self, tmp_path):
        """★ 反向断言：register() 走 bootstrap 分支时**只记一次账**。

        把限额设成 1：若实现里 register 与 bootstrap 各记一次，
        第二次记账就会抛 RateLimitError，这次注册必然失败。
        所以「这次注册成功」本身就证明了没有重复记账。
        （和「一次抽取耗两档」是同类坑，能避就避。）
        """
        settings = _settings(tmp_path, register=1)
        store = _user_store(settings)
        user = store.register("first", GOOD_PW)  # 只该消耗 1 格
        assert user.role == "admin", "首启注册应拿到 admin"

    def test_key_is_global_not_per_username(self, tmp_path):
        """★ 证明用的是全局桶：换用户名也照样受限（防换名刷号的关键语义）。"""
        settings = _settings(tmp_path, register=2)
        store = _user_store(settings)
        for i in range(2):
            store.register(f"user{i}", GOOD_PW)
        with pytest.raises(RateLimitError):
            store.register("totally-different-name", GOOD_PW)

    def test_closed_signup_does_not_consume_quota(self, tmp_path):
        """注册功能关着时不该消耗配额 —— 那不是攻击行为，别让运维白掉配额。"""
        settings_open = _settings(tmp_path, register=2)
        store_open = _user_store(settings_open)
        store_open.bootstrap("admin", GOOD_PW)  # 用掉 1 格

        # 注册关着的同一套配置（限流器是进程级单例，两个 store 共用同一个桶）
        settings_closed = settings_open.model_copy(update={"allow_signup": False})
        store_closed = _user_store(settings_closed)
        for _ in range(5):
            with pytest.raises(RegistrationDisabled):
                store_closed.register("someone", GOOD_PW)

        # 若上面 5 次消耗了配额，这里就会抛 RateLimitError
        assert store_open.register("legit", GOOD_PW).username == "legit"

    def test_register_quota_does_not_block_login(self, tmp_path):
        """开号档与登录档是**独立两档**：开号刷满不该影响已注册用户登录。"""
        settings = _settings(tmp_path, register=1, login=2)
        store = _user_store(settings)
        store.bootstrap("admin", GOOD_PW)  # 开号档已用尽
        with pytest.raises(RateLimitError):
            store.register("extra", GOOD_PW)
        # 登录照常（用的是 login:{username} 另一个桶）
        assert store.authenticate("admin", GOOD_PW).username == "admin"


# ══════════════════════════════════════════════════════════
# 证明 2 / 四档之二：聊天限流（真实调用链：LLMService.chat / stream）
# ══════════════════════════════════════════════════════════
class TestChatTierIsWired:
    def test_chat_entry_charges_quota(self, tmp_path, fake_registry):
        fake_registry.register("p1", FakeProvider("主"))
        svc = LLMService(_settings(tmp_path, chat=3), "alice", registry=fake_registry)

        for _ in range(3):
            svc.chat(_provider(), MSG)

        with pytest.raises(RateLimitError):
            svc.chat(_provider(), MSG)

    def test_stream_entry_charges_quota(self, tmp_path, fake_registry):
        fake_registry.register("p1", FakeProvider("主"))
        svc = LLMService(_settings(tmp_path, chat=3), "alice", registry=fake_registry)

        for _ in range(3):
            assert list(svc.stream(_provider(), MSG))[-1].kind == "end"

        events = list(svc.stream(_provider(), MSG))
        assert isinstance(events[0], ErrorEvent), "第 4 次 stream 应被限流"

    def test_chat_and_stream_share_one_quota(self, tmp_path, fake_registry):
        """★ 证明 4：chat() 与 stream() 必须共用同一档配额。

        混着调用也一样会耗尽 —— 否则「只堵一个入口」的漏洞就能被这条测试抓住。
        """
        fake_registry.register("p1", FakeProvider("主"))
        svc = LLMService(_settings(tmp_path, chat=4), "alice", registry=fake_registry)

        svc.chat(_provider(), MSG)          # 1
        list(svc.stream(_provider(), MSG))  # 2
        svc.chat(_provider(), MSG)          # 3
        list(svc.stream(_provider(), MSG))  # 4 —— 用满

        with pytest.raises(RateLimitError):
            svc.chat(_provider(), MSG)
        assert isinstance(next(iter(svc.stream(_provider(), MSG))), ErrorEvent)

    def test_stream_degrades_to_error_event_not_exception(self, tmp_path, fake_registry):
        """stream() 的契约是「事件流总有始有终」：超限要产 ErrorEvent，**不能抛异常**。

        若这里抛异常，UI 的线性 for 循环会把异常冒出去变成整页红框。
        """
        fake_registry.register("p1", FakeProvider("主"))
        svc = LLMService(_settings(tmp_path, chat=1), "alice", registry=fake_registry)
        list(svc.stream(_provider(), MSG))  # 用满

        events = list(svc.stream(_provider(), MSG))  # 不抛异常
        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].retryable is True
        assert events[0].user_message  # 有给人看的话


# ══════════════════════════════════════════════════════════
# 证明 3 / 三档之三：记忆抽取限流（真实调用链：MemoryService）
# ══════════════════════════════════════════════════════════
class TestExtractTierIsWired:
    def test_extract_entry_charges_quota(self, tmp_path, fake_registry):
        """把 chat 档调到很高，只让 extract 档成为瓶颈 —— 这样才**归因到抽取档本身**。"""
        settings = _settings(tmp_path, chat=999, extract=3)
        fake_registry.register("p1", FakeProvider("抽取", reply='["一条事实"]'))
        mem = _memory_service(settings, "alice", fake_registry)

        for _ in range(3):
            mem.remember_from_exchange("c1", MSG, _provider())

        with pytest.raises(RateLimitError):
            mem.remember_from_exchange("c1", MSG, _provider())

    def test_extract_is_skipped_when_no_provider(self, tmp_path, fake_registry):
        """没有 provider 时直接返回 0，不消耗配额（早退分支不该记账）。"""
        settings = _settings(tmp_path, chat=999, extract=1)
        mem = _memory_service(settings, "alice", fake_registry)
        for _ in range(5):
            assert mem.remember_from_exchange("c1", MSG, None) == 0
        # 配额仍然是满的 → 不被早退分支消耗
        mem.remember_from_exchange("c1", MSG, _provider())  # 应该还能用

    def test_one_extraction_consumes_two_tiers(self, tmp_path, fake_registry):
        """★ 如实固定「一次抽取消耗两档」这个相互作用。

        抽取内部会调 `llm.chat()`，所以它同时吃 extract 档与 chat 档。
        这不是 bug，是有意的（抽取=额外一次模型调用），但必须被测试固化 ——
        否则将来有人改 default 值时会不知道这个耦存在。
        """
        settings = _settings(tmp_path, chat=2, extract=999)
        fake_registry.register("p1", FakeProvider("抽取", reply='["事实"]'))
        mem = _memory_service(settings, "alice", fake_registry)

        mem.remember_from_exchange("c1", MSG, _provider())  # extract 1 + chat 1
        mem.remember_from_exchange("c1", MSG, _provider())  # extract 2 + chat 2（chat 用满）

        # 第 3 次：extract 档还有余量，但内部 llm.chat() 的 chat 档已满
        with pytest.raises(RateLimitError):
            mem.remember_from_exchange("c1", MSG, _provider())


# ══════════════════════════════════════════════════════════
# 证明 4：按用户/按用户名隔离（一人刷满不能锁死别人）
# ══════════════════════════════════════════════════════════
class TestQuotaIsolation:
    def test_chat_quota_isolated_between_users(self, tmp_path, fake_registry):
        fake_registry.register("p1", FakeProvider("主"))
        settings = _settings(tmp_path, chat=2)
        alice = LLMService(settings, "alice", registry=fake_registry)
        bob = LLMService(settings, "bob", registry=fake_registry)

        alice.chat(_provider(), MSG)
        alice.chat(_provider(), MSG)
        with pytest.raises(RateLimitError):
            alice.chat(_provider(), MSG)

        # bob 完全没被影响
        assert bob.chat(_provider(), MSG).text

    def test_login_quota_isolated_between_usernames(self, tmp_path):
        settings = _settings(tmp_path, login=2)
        store = _user_store(settings)
        store.bootstrap("alice", GOOD_PW)
        store.admin_create_user("bob", GOOD_PW)

        store.authenticate("alice", GOOD_PW)
        store.authenticate("alice", GOOD_PW)
        with pytest.raises(RateLimitError):
            store.authenticate("alice", GOOD_PW)

        # 换个用户名不受影响
        assert store.authenticate("bob", GOOD_PW).username == "bob"

    def test_extract_quota_isolated_between_users(self, tmp_path, fake_registry):
        settings = _settings(tmp_path, chat=999, extract=1)
        fake_registry.register("p1", FakeProvider("抽取", reply='["事实"]'))
        alice = _memory_service(settings, "alice", fake_registry)
        bob = _memory_service(settings, "bob", fake_registry)

        alice.remember_from_exchange("c1", MSG, _provider())
        with pytest.raises(RateLimitError):
            alice.remember_from_exchange("c1", MSG, _provider())

        bob.remember_from_exchange("c1", MSG, _provider())  # 不受影响


# ══════════════════════════════════════════════════════════
# 证明 5：ServiceContainer 真的把 user_id 注入下去了
# ══════════════════════════════════════════════════════════
class TestContainerInjectsUserId:
    """用**容器自己持有的** service 验证，而不是 set_llm() 换一个进去。

    为什么这很关键：`set_llm()` 会把测试自己构造的服务塞进去，
    那样就验证不到「容器有没有正确注入 user_id」——
    而「注入漏了」正是这次改动最容易犯的错。
    """

    def test_container_meters_its_own_llm_per_user(self, tmp_path, fake_registry):
        fake_registry.register("p1", FakeProvider("主"))
        settings = _settings(tmp_path, chat=2)

        alice = ServiceContainer(settings, "alice", registry=fake_registry)
        bob = ServiceContainer(settings, "bob", registry=fake_registry)

        alice.llm.chat(_provider(), MSG)
        alice.llm.chat(_provider(), MSG)
        with pytest.raises(RateLimitError):
            alice.llm.chat(_provider(), MSG)

        # ★ 若容器漏传 user_id，两人会共用 chat:anonymous → bob 也会被锁 → 这里失败
        assert bob.llm.chat(_provider(), MSG).text

    def test_container_meters_its_own_memory_per_user(self, tmp_path, fake_registry):
        fake_registry.register("p1", FakeProvider("抽取", reply='["事实"]'))
        settings = _settings(tmp_path, chat=999, extract=1)

        alice = ServiceContainer(settings, "alice", registry=fake_registry)
        bob = ServiceContainer(settings, "bob", registry=fake_registry)

        alice.memory.remember_from_exchange("c1", MSG, _provider())
        with pytest.raises(RateLimitError):
            alice.memory.remember_from_exchange("c1", MSG, _provider())

        bob.memory.remember_from_exchange("c1", MSG, _provider())

    def test_container_owned_services_see_the_user_id(self, tmp_path):
        """直接断言注入点（比行为断言更直白地指向"构造注入"这件事）。"""
        settings = _settings(tmp_path)
        alice = ServiceContainer(settings, "alice")
        assert alice.llm.user_id == "alice"
        assert alice.memory.user_id == "alice"
        assert alice.user_id == "alice"


# ══════════════════════════════════════════════════════════
# 反向证明：限流的 key 真的带上了身份前缀
# ══════════════════════════════════════════════════════════
class TestQuotaKeysCarryIdentity:
    def test_anonymous_key_is_not_shared_with_named_users(self, tmp_path, fake_registry):
        """没传 user_id 的服务用 `chat:anonymous`，**不能**影响具名用户。"""
        fake_registry.register("p1", FakeProvider("主"))
        settings = _settings(tmp_path, chat=1)

        anon = LLMService(settings, "", registry=fake_registry)
        named = LLMService(settings, "alice", registry=fake_registry)

        anon.chat(_provider(), MSG)  # 用满 anonymous
        with pytest.raises(RateLimitError):
            anon.chat(_provider(), MSG)

        assert named.chat(_provider(), MSG).text  # alice 不受影响

    def test_username_key_is_case_insensitive(self, tmp_path):
        """`Admin` 与 `admin` 必须算同一个桶，否则改大小写就能绕过限流。"""
        settings = _settings(tmp_path, login=1)
        store = _user_store(settings)
        store.bootstrap("admin", GOOD_PW)

        store.authenticate("admin", GOOD_PW)  # 用满
        with pytest.raises(RateLimitError):
            store.authenticate("ADMIN", GOOD_PW)  # 换大小写也逃不掉

    def test_end_event_still_reports_usage_when_not_limited(self, tmp_path, fake_registry):
        """限流不能把正常路径搞坏：没过限时仍应正常 EndEvent + 用量。"""
        fake_registry.register("p1", FakeProvider("主"))
        svc = LLMService(_settings(tmp_path, chat=10), "alice", registry=fake_registry)
        events = list(svc.stream(_provider(), MSG))
        assert isinstance(events[-1], EndEvent)
        assert events[-1].usage.total_tokens == 15
