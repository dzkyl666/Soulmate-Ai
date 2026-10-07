"""auth 层测试：账号库 / 登录 / 注册开关 / 停用 / 会话令牌 / OIDC 映射。"""

from __future__ import annotations

import pytest

from soulmate.auth.session import issue_session_token, read_session_token
from soulmate.auth.user_store import UserStore
from soulmate.core.exceptions import (
    AccountDisabled,
    InvalidCredentials,
    RegistrationDisabled,
    ValidationError,
)
from soulmate.core.settings import Settings
from soulmate.storage.repositories import UserRepository

GOOD_PW = "Passw0rd123"


@pytest.fixture
def store(settings) -> UserStore:
    return UserStore(UserRepository(settings.data_root()), settings)


# ══════════════════════════════════════════════════════════
# 首次初始化
# ══════════════════════════════════════════════════════════
class TestBootstrap:
    def test_needs_bootstrap_when_empty(self, store):
        assert store.needs_bootstrap() is True
        assert store.count() == 0

    def test_first_user_becomes_admin(self, store):
        user = store.bootstrap("admin", GOOD_PW)
        assert user.role == "admin"
        assert store.needs_bootstrap() is False
        assert store.is_admin("admin") is True

    def test_bootstrap_rejects_weak_password(self, store):
        with pytest.raises(ValidationError):
            store.bootstrap("admin", "123")

    def test_bootstrap_rejects_bad_username(self, store):
        with pytest.raises(ValidationError):
            store.bootstrap("a", GOOD_PW)


# ══════════════════════════════════════════════════════════
# 注册
# ══════════════════════════════════════════════════════════
class TestRegister:
    def test_register_creates_regular_user(self, store):
        store.bootstrap("admin", GOOD_PW)
        user = store.register("bob", GOOD_PW)
        assert user.role == "user"
        assert store.is_admin("bob") is False

    def test_register_when_closed_raises(self, settings):
        strict = Settings(
            env="test", app_secret="s" * 48, data_dir=settings.data_root(),
            allow_signup=False, legacy_session_dir=settings.legacy_session_dir,
        )
        s = UserStore(UserRepository(strict.data_root()), strict)
        s.bootstrap("admin", GOOD_PW)
        with pytest.raises(RegistrationDisabled):
            s.register("bob", GOOD_PW)

    def test_register_duplicate_raises(self, store):
        store.bootstrap("admin", GOOD_PW)
        store.register("bob", GOOD_PW)
        with pytest.raises(ValidationError):
            store.register("bob", GOOD_PW)

    def test_register_weak_password_raises(self, store):
        store.bootstrap("admin", GOOD_PW)
        with pytest.raises(ValidationError):
            store.register("bob", "weakpass")

    def test_first_register_becomes_admin_even_without_bootstrap(self, store):
        """没走 bootstrap 直接注册，第一个也该是 admin。"""
        user = store.register("first", GOOD_PW)
        assert user.role == "admin"


# ══════════════════════════════════════════════════════════
# 登录
# ══════════════════════════════════════════════════════════
class TestAuthenticate:
    def test_success(self, store):
        store.bootstrap("admin", GOOD_PW)
        assert store.authenticate("admin", GOOD_PW).username == "admin"

    def test_username_case_insensitive(self, store):
        store.bootstrap("admin", GOOD_PW)
        assert store.authenticate("ADMIN", GOOD_PW).username == "admin"

    def test_wrong_password_raises_invalid_credentials(self, store):
        store.bootstrap("admin", GOOD_PW)
        with pytest.raises(InvalidCredentials):
            store.authenticate("admin", "WrongPass123")

    def test_unknown_user_raises_same_error(self, store):
        """不区分「用户不存在」和「密码错」—— 防止用户名枚举。"""
        store.bootstrap("admin", GOOD_PW)
        with pytest.raises(InvalidCredentials):
            store.authenticate("nobody", GOOD_PW)

    def test_disabled_account_raises(self, store):
        store.bootstrap("admin", GOOD_PW)
        store.register("bob", GOOD_PW)
        store.set_disabled("bob", True)
        with pytest.raises(AccountDisabled):
            store.authenticate("bob", GOOD_PW)

    def test_reenable(self, store):
        store.bootstrap("admin", GOOD_PW)
        store.register("bob", GOOD_PW)
        store.set_disabled("bob", True)
        store.set_disabled("bob", False)
        assert store.authenticate("bob", GOOD_PW).username == "bob"

    def test_set_disabled_unknown_user_raises(self, store):
        with pytest.raises(ValidationError):
            store.set_disabled("ghost", True)


# ══════════════════════════════════════════════════════════
# 管理操作
# ══════════════════════════════════════════════════════════
class TestAdminOps:
    def test_admin_create_bypasses_closed_signup(self, settings):
        strict = Settings(
            env="test", app_secret="s" * 48, data_dir=settings.data_root(),
            allow_signup=False, legacy_session_dir=settings.legacy_session_dir,
        )
        s = UserStore(UserRepository(strict.data_root()), strict)
        s.bootstrap("admin", GOOD_PW)
        user = s.admin_create_user("newbie", GOOD_PW)
        assert user.username == "newbie"

    def test_admin_create_with_role(self, store):
        store.bootstrap("admin", GOOD_PW)
        assert store.admin_create_user("second", GOOD_PW, role="admin").role == "admin"

    def test_admin_create_rejects_bad_role(self, store):
        store.bootstrap("admin", GOOD_PW)
        with pytest.raises(ValidationError):
            store.admin_create_user("x", GOOD_PW, role="superuser")

    def test_admin_create_duplicate_raises(self, store):
        store.bootstrap("admin", GOOD_PW)
        with pytest.raises(ValidationError):
            store.admin_create_user("admin", GOOD_PW)

    def test_delete_user_keeps_data_dir(self, store, settings):
        """删账号不删数据目录（数据可能还要抢救）。"""
        store.bootstrap("admin", GOOD_PW)
        store.register("bob", GOOD_PW)
        data_dir = settings.user_data_dir("bob")
        data_dir.mkdir(parents=True, exist_ok=True)
        store.delete_user("bob")
        assert store.list_users()[0].username == "admin"
        assert data_dir.exists()

    def test_list_users(self, store):
        store.bootstrap("admin", GOOD_PW)
        store.register("bob", GOOD_PW)
        assert {u.username for u in store.list_users()} == {"admin", "bob"}


# ══════════════════════════════════════════════════════════
# OIDC 用户
# ══════════════════════════════════════════════════════════
class TestOidcUsers:
    def test_ensure_oidc_user_creates_without_password(self, store):
        user = store.ensure_oidc_user(username="oidc_alice_abc", oidc_sub="alice@x.com", display_name="alice@x.com")
        assert user.password_hash == ""
        assert user.oidc_sub == "alice@x.com"
        assert user.role == "admin"  # 第一个用户

    def test_ensure_oidc_user_is_idempotent(self, store):
        a = store.ensure_oidc_user(username="oidc_alice_abc", oidc_sub="alice@x.com", display_name="a")
        b = store.ensure_oidc_user(username="oidc_alice_abc", oidc_sub="alice@x.com", display_name="a")
        assert a.username == b.username and store.count() == 1

    def test_oidc_user_cannot_password_login(self, store):
        store.ensure_oidc_user(username="oidc_alice_abc", oidc_sub="alice@x.com", display_name="a")
        with pytest.raises(InvalidCredentials):
            store.authenticate("oidc_alice_abc", "")


class TestOidcMapping:
    def test_stable_username_is_safe_and_deterministic(self):
        from soulmate.auth import oidc

        ident = {"email": "Alice.Wang@Example.com", "sub": "12345"}
        u1 = oidc.stable_username(ident)
        u2 = oidc.stable_username(ident)
        assert u1 == u2
        assert u1.startswith("oidc_")
        # 必须只含安全字符（会成为目录名）
        assert all(c.isalnum() or c in "_-." for c in u1)
        assert "@" not in u1

    def test_different_identities_differ(self):
        from soulmate.auth import oidc

        assert oidc.stable_username({"email": "a@x.com"}) != oidc.stable_username({"email": "b@x.com"})

    def test_falls_back_to_sub_then_random(self):
        from soulmate.auth import oidc

        assert oidc.stable_username({"sub": "abc"}).startswith("oidc_")
        assert oidc.stable_username({}).startswith("oidc_")

    def test_display_name_prefers_email(self):
        from soulmate.auth import oidc

        assert oidc.display_name({"email": "a@x.com", "name": "Alice"}) == "a@x.com"


# ══════════════════════════════════════════════════════════
# 会话令牌
# ══════════════════════════════════════════════════════════
class TestSessionTokens:
    def test_issue_and_read(self, settings):
        token = issue_session_token("alice", settings)
        assert read_session_token(token, settings) == "alice"

    def test_wrong_secret_rejected(self, settings, tmp_path):
        token = issue_session_token("alice", settings)
        other = Settings(env="test", app_secret="z" * 48, data_dir=tmp_path / "d2")
        assert read_session_token(token, other) is None

    def test_garbage_rejected(self, settings):
        assert read_session_token("nonsense", settings) is None
        assert read_session_token("", settings) is None