"""认证门面（AuthService）：UI 只跟它打交道，不直接碰仓储。

【为什么需要这一层】
分层铁律是 `ui → services → storage`。但认证是**登录前**就要用的能力 ——
那时还没有 `ServiceContainer`（容器需要 user_id，而 user_id 恰恰来自登录）。
于是 UI 曾经写成 `UserStore(UserRepository(settings.data_root()), settings)`，
那是两处实打实的越层（`ui → storage`），也是验收方 AST 扫描抓到的两处违规。

本类把「怎么拿到 UserStore」收进 services 层：
- **登录门**（`ui/app.py`、`ui/auth_page.py`）：`AuthService(settings)` 独立构造，
  不依赖用户上下文；
- **登录后**（`ui/sidebar.py`、`ui/settings_page.py`）：用容器上的 `svc.auth`，
  同一套接口、同一份实现。

【它明确不做的事】
**不包 OIDC**。`auth/oidc.py` 是 Streamlit 登录适配器（内部要调 `st.login()`），
硬塞进 services 会让 services 层依赖 streamlit —— 那是比原来更严重的越层。
所以 `ui → auth.oidc` 被显式保留为一个**有理由的例外**，
理由与边界写在 `docs/ARCHITECTURE.md` 的「分层例外」一节，并纳入 verify.py 的矩阵检查。
"""

from __future__ import annotations

from typing import Literal

from soulmate.auth.user_store import UserStore
from soulmate.core.models import UserRecord
from soulmate.core.settings import Settings
from soulmate.storage.repositories import UserRepository


class AuthService:
    """认证用例的对外接口。UI 拿到它就能完成注册/登录/账号管理。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store = UserStore(UserRepository(settings.data_root()), settings)

    # ── 状态（登录页据此决定显示「创建管理员」还是「登录」）──
    def needs_bootstrap(self) -> bool:
        return self._store.needs_bootstrap()

    def count(self) -> int:
        return self._store.count()

    def is_admin(self, username: str) -> bool:
        return self._store.is_admin(username)

    # ── 登录门用到的流程 ──
    def bootstrap(self, username: str, password: str) -> UserRecord:
        """首个用户：直接建成管理员。"""
        return self._store.bootstrap(username, password)

    def register(self, username: str, password: str) -> UserRecord:
        """自助注册（受 `allow_signup` 约束）。"""
        return self._store.register(username, password)

    def authenticate(self, username: str, password: str) -> UserRecord:
        """校验账号密码；失败抛 `InvalidCredentials` / `AccountDisabled` / `RateLimitError`。"""
        return self._store.authenticate(username, password)

    def ensure_oidc_user(self, *, username: str, oidc_sub: str, display_name: str) -> UserRecord:
        """OIDC 身份 → 本地账号（没有就建）。"""
        return self._store.ensure_oidc_user(username=username, oidc_sub=oidc_sub, display_name=display_name)

    # ── 管理员面板用到的管理动作 ──
    def list_users(self) -> list[UserRecord]:
        return self._store.list_users()

    def admin_create_user(
        self, username: str, password: str, *, role: Literal["admin", "user"] = "user"
    ) -> UserRecord:
        """管理员开号：不受 `allow_signup` 关门限制（这是有意的）。"""
        return self._store.admin_create_user(username, password, role=role)

    def set_disabled(self, username: str, disabled: bool) -> None:
        self._store.set_disabled(username, disabled)

    def delete_user(self, username: str) -> None:
        """删账号记录（**不**删数据目录）。"""
        self._store.delete_user(username)
