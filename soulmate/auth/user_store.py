"""本机账号库：注册 / 登录 / 管理员 / 停用。

【安全要点】
- 密码只存 bcrypt 哈希，绝不存明文；
- 登录失败不区分「用户不存在」和「密码错误」（统一提示，防用户名枚举）；
- 第一个用户自动成为 admin（bootstrap）；生产环境 `allow_signup` 关闭后
  只能靠 `soulmate create-user` 命令行或管理员 UI 开号。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from soulmate.core.exceptions import (
    AccountDisabled,
    InvalidCredentials,
    RegistrationDisabled,
    ValidationError,
)
from soulmate.core.logging import get_logger
from soulmate.core.models import UserRecord
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
    def bootstrap(self, username: str, password: str) -> UserRecord:
        """第一个用户：直接给 admin 角色。"""
        user = self._new_user(username, password)
        user.role = "admin"
        self._repo.upsert(user)
        return user

    def register(self, username: str, password: str) -> UserRecord:
        """自助注册。未开放注册 / 重名 / 弱密码都会抛出明确异常。"""
        if self.needs_bootstrap():  # 还没 admin，先 bootstrap
            return self.bootstrap(username, password)
        if not self._settings.allow_signup:
            raise RegistrationDisabled()
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
        if self.needs_bootstrap():
            role = "admin"
        else:
            role = "user"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
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

    def admin_create_user(self, username: str, password: str, *, role: str = "user") -> UserRecord:
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

    def _new_user(self, username: str, password: str, *, role: str = "user") -> UserRecord:
        username = validate_username(username)
        problems = password_strength(password)
        if problems:
            raise ValidationError("密码不够强：" + "、".join(problems))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return UserRecord(
            username=username,
            password_hash=hash_password(password) if password else "",
            role=role,  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
        )

    # ── 认证 ──
    def authenticate(self, username: str, password: str) -> UserRecord:
        """校验账号密码。任何失败统一抛 InvalidCredentials（防枚举）。"""
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
        user.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._repo.upsert(user)

    def delete_user(self, username: str) -> None:
        """删除账号记录。**不**删数据目录（数据可能还有用，删错了找不回来）。"""
        self._repo.delete(username)