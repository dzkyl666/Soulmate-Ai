"""本机账号库：注册 / 登录 / 管理员 / 停用。

【安全要点】
- 密码只存 bcrypt 哈希，绝不存明文；
- 登录失败不区分「用户不存在」和「密码错误」（统一提示，防用户名枚举）；
- 第一个用户自动成为 admin（bootstrap）；生产环境 `allow_signup` 关闭后
  只能靠 `soulmate create-user` 命令行或管理员 UI 开号。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from soulmate.core.exceptions import (
    AccountDisabled,
    InvalidCredentials,
    RegistrationDisabled,
    ValidationError,
)
from soulmate.core.logging import get_logger
from soulmate.core.models import UserRecord
from soulmate.core.ratelimit import get_limiter
from soulmate.core.security import hash_password, password_strength, verify_password
from soulmate.core.settings import Settings
from soulmate.core.validation import validate_username
from soulmate.storage.repositories import UserRepository

log = get_logger("soulmate.auth.user_store")

#: 供 UI 判断"该展示哪个入口"的错误类型
REGISTRATION_CLOSED = "registration_closed"


class UserStore:
    def __init__(self, users_repo: UserRepository, settings: Settings) -> None:
        self._repo = users_repo
        self._settings = settings

    # ── 状态 ──
    def needs_bootstrap(self) -> bool:
        """一个用户都没有 → 首页显示「创建管理员账号」。"""
        return len(self._repo.list()) == 0

    def count(self) -> int:
        return len(self._repo.list())

    # ── 创建 ──
    def _charge_register_quota(self) -> None:
        """开号配额记账。`bootstrap()` 与 `register()` 共用，但**不重复计**。

        【为什么 key 是全局（`register:global`）而不是 username】
        登录那档按 username 记是有道理的（要防定向爆破某个账号）；
        但注册不一样：攻击者刷号时会**不断换用户名**，按 username 记账
        等于每条都从一个空桶开始 —— 记了等于没记。所以这里用全局桶，
        限制的是「整个进程每分钟最多开多少个号」，这才是真实风险面。

        【代价，如实记录】
        全局桶意味着理论上可以被用来**阻塞他人注册**（把配额刷满，
        真用户这一分钟内就注册不了）。对个人 / 小团队应用这是可接受的：
        真有人刷号时，你本来就希望先停下来。而且生产环境的首要控制不是限流，
        而是 `SOULMATE_ALLOW_SIGNUP=false`（`SOULMATE_ENV=production` 时会强制校验）。
        """
        get_limiter().hit(
            "register:global",
            limit=self._settings.rate_limit_register_per_minute,
            per_seconds=60,
        )

    def bootstrap(self, username: str, password: str) -> UserRecord:
        """第一个用户：直接给 admin 角色。

        【为什么首启也记账】首次初始化是「谁先到谁当管理员」的窗口，
        没人管的话可以被脚本抢占。记账后刷号速率被限制住。
        """
        self._charge_register_quota()
        user = self._new_user(username, password)
        user.role = "admin"
        self._repo.upsert(user)
        return user

    def register(self, username: str, password: str) -> UserRecord:
        """自助注册。未开放注册 / 重名 / 弱密码 / 开号过快都会抛出明确异常。

        【记账位置的两点刻意设计】
        1. 走 bootstrap 分支时**不在本方法再记一次** —— bootstrap 内部已经记过，
           否则一次注册会消耗两格配额（和"一次抽取耗两档"是同类坑，能避就避）。
        2. 记账放在 `allow_signup` 判断**之后** —— 「注册功能关着」不是攻击行为，
           不该消耗配额、不该让运维在关着注册时还看到配额被吃掉。
           但重名/弱密码检查在记账**之后**，所以刷重名同样受限。
        """
        if self.needs_bootstrap():  # 还没 admin，先 bootstrap（内部已记账）
            return self.bootstrap(username, password)
        if not self._settings.allow_signup:
            raise RegistrationDisabled()
        self._charge_register_quota()
        if self._repo.exists(username):
            raise ValidationError("用户名已存在")
        problems = password_strength(password)
        if problems:
            raise ValidationError("密码不够强：" + "、".join(problems))
        user = self._new_user(username, password, role="user")
        self._repo.upsert(user)
        return user

    def ensure_oidc_user(self, *, username: str, oidc_sub: str, display_name: str) -> UserRecord:
        """OIDC 登录时按稳定身份找/建本地账号。不会设置密码。"""
        existing = self._repo.get(username)
        if existing:
            return existing
        role: Literal["admin", "user"] = "admin" if self.needs_bootstrap() else "user"
        now = datetime.now(UTC).isoformat(timespec="seconds")
        user = UserRecord(
            username=username,
            password_hash="",  # OIDC 用户无本地口令
            role=role,
            disabled=False,
            oidc_sub=oidc_sub,
            display_name=display_name,
            created_at=now,
            updated_at=now,
        )
        self._repo.upsert(user)
        return user

    def admin_create_user(
        self, username: str, password: str, *, role: Literal["admin", "user"] = "user"
    ) -> UserRecord:
        """管理员/命令行开号：不受 `allow_signup` 关门的限制（这是有意的）。

        生产环境关闭自助注册后，新用户只能由此入口创建 —— 这一点要写进文档。
        """
        if role not in ("admin", "user"):
            raise ValidationError("角色只能是 admin 或 user")
        if self._repo.exists(username):
            raise ValidationError("用户名已存在")
        user = self._new_user(username, password, role=role)
        self._repo.upsert(user)
        return user

    def _new_user(
        self, username: str, password: str, *, role: Literal["admin", "user"] = "user"
    ) -> UserRecord:
        username = validate_username(username)
        problems = password_strength(password)
        if problems:
            raise ValidationError("密码不够强：" + "、".join(problems))
        now = datetime.now(UTC).isoformat(timespec="seconds")
        return UserRecord(
            username=username,
            password_hash=hash_password(password) if password else "",
            role=role,
            created_at=now,
            updated_at=now,
        )

    # ── 认证 ──
    def authenticate(self, username: str, password: str) -> UserRecord:
        """校验账号密码。任何失败统一抛 InvalidCredentials（防枚举）。

        【限流 key 为什么是 username，而不是 user_id】
        走到这里时用户**尚未认证**，拿不到 user_id —— 只能用「他声称是谁」。

        【这个取舍的代价，如实记录】
        按用户名限流 ⇒ 攻击者可以**定向锁死某个账号**：只要针对 `admin` 刷满配额，
        真管理员在窗口内就登不进来（拒绝服务型副作用）。
        这是标准取舍（备选是叠加客户端 IP，但 Streamlit 侧拿真实 IP 不可靠，
        反向代理下还会拿到代理 IP）。缓解与权衡写在 docs/SECURITY.md。
        """
        get_limiter().hit(
            f"login:{username.strip().lower() or '-'}",
            limit=self._settings.rate_limit_login_per_minute,
            per_seconds=60,
        )

        user = self._repo.get(username)
        if user is None or not verify_password(password, user.password_hash):
            # 故意统一文案，不给攻击者「用户名存在吗」的信号
            raise InvalidCredentials()
        if user.disabled:
            raise AccountDisabled()
        return user

    # ── 管理 ──
    def is_admin(self, username: str) -> bool:
        user = self._repo.get(username)
        return bool(user and user.role == "admin")

    def list_users(self) -> list[UserRecord]:
        return self._repo.list()

    def set_disabled(self, username: str, disabled: bool) -> None:
        user = self._repo.get(username)
        if user is None:
            raise ValidationError("用户不存在")
        user.disabled = bool(disabled)
        user.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._repo.upsert(user)

    def delete_user(self, username: str) -> None:
        """删除账号记录。**不**删数据目录（数据可能还有用，删错了找不回来）。"""
        self._repo.delete(username)
