"""应用配置：环境变量 + `.env` 文件，全部带默认值，开箱即跑。

【设计原则】
1. **配置只有一个来源**：所有可调项都在这一个类里，别处不许再散落 `os.getenv()`。
   散落是 bug 的温床 —— 你会忘了还有哪些地方能改，也会忘了同步。
2. **默认值必须能跑通单机**：不设任何环境变量也能 `streamlit run main.py` 起来。
3. **生产项必须显式开启**：`SOULMATE_ENV=production` 时，弱密钥 / 明文口令这类
   危险配置会直接拒绝启动（fail fast），而不是等出事。
4. **不碰用户级环境变量**：本项目只读进程环境变量和项目根的 `.env`，
   绝不 `setx` 往系统里塞东西。

【怎么改配置】
- 本地开发：项目根建 `.env`（模板见 `.env.example`），或用默认值。
- 容器/服务器：通过环境变量注入；密钥走 secrets，不进镜像。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：本文件是 <root>/soulmate/core/settings.py → parents[2] 即 <root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 环境名。单用户本机跑用 development / local；部署到服务器且可能多人访问用 production
EnvName = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """全部运行期配置。字段名即环境变量名（自动大写 + 前缀 `SOULMATE_`）。"""

    model_config = SettingsConfigDict(
        env_prefix="SOULMATE_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 基础 ────────────────────────────────────────────────
    app_name: str = "Soulmate AI"
    env: EnvName = "development"
    debug: bool = False
    """打开后：日志降级为 DEBUG、界面显示异常堆栈。生产必须 False。"""

    # ── 数据落盘 ────────────────────────────────────────────
    data_dir: Path = PROJECT_ROOT / "data"
    """数据根目录。结构见 `data_root()` / `user_data_dir()`。"""

    legacy_session_dir: Path | None = None
    """v0 老版本的 `项目根/session/` 目录位置（迁移用）。默认取项目根；测试可改指临时目录。"""

    @property
    def legacy_session_path(self) -> Path:
        return Path(self.legacy_session_dir or PROJECT_ROOT / "session")

    # ── 安全 ────────────────────────────────────────────────
    app_secret: str = ""
    """根密钥：用于派生「API Key 加密用」的 Fernet key 和会话签名 key。

    留空时：非生产环境会自动生成一个并落盘到 `<data>/.app_secret`（方便开箱即跑）；
    生产环境留空 → 拒绝启动，因为密钥一重启就变会导致已存数据无法解密。
    """

    allow_private_base_url: bool = False
    """是否允许把模型地址指向内网/回环地址（如 Ollama、内网反代）。

    默认 False：防止「用户填一个 URL，服务器替他去请求内网」这类 SSRF。
    自己确实需要接内网服务时，显式设 `SOULMATE_ALLOW_PRIVATE_BASE_URL=true`。
    """

    session_ttl_hours: int = Field(default=72, ge=1, le=24 * 30)
    """登录态有效期（小时）。"""

    # ── 认证 ────────────────────────────────────────────────
    auth_mode: Literal["local", "oidc", "hybrid"] = "local"
    """`local` 用本机账号库；`oidc` 用 Streamlit 原生 `st.login()`；
    `hybrid` 两者都开（OIDC 优先，失败可退回本机账号）。"""

    allow_signup: bool = True
    """是否允许自助注册。公开部署建议关掉，改由管理员开号。"""

    admin_usernames: str = "admin"
    """管理员用户名，逗号分隔。管理员可管理账号、看全局诊断。"""

    # ── 限流（同一进程内的令牌桶；多进程部署需换 Redis，见文档）──
    rate_limit_chat_per_minute: int = Field(default=30, ge=1)
    rate_limit_extract_per_minute: int = Field(default=60, ge=1)
    rate_limit_login_per_minute: int = Field(default=10, ge=1)

    # ── 对话 ────────────────────────────────────────────────
    default_history_length: int = Field(default=15, ge=1, le=100)
    max_history_length: int = Field(default=30, ge=1, le=200)
    max_message_chars: int = Field(default=8000, ge=100)
    """单条用户消息的字符上限。防止有人贴一本小说进来把 token 烧光。"""

    max_context_chars: int = Field(default=24000, ge=1000)
    """送进模型的历史总字符预算。超出时从最旧的消息开始丢（保留 system）。"""

    request_timeout_seconds: float = Field(default=60.0, gt=0)
    """普通对话超时。注意必须给上界：httpx 不设超时会一直挂着。"""

    extract_timeout_seconds: float = Field(default=15.0, gt=0)
    """长期记忆抽取超时。比对话短，因为它是「顺手做的事」。"""

    max_retries: int = Field(default=2, ge=0, le=10)
    """可重试错误（网络抖动 / 429 / 5xx）的额外重试次数。"""

    retry_backoff_seconds: float = Field(default=0.8, gt=0)
    """指数退避基数：第 n 次重试等 `base * 2**(n-1)` 秒，并加随机抖动。"""

    # ── 长期记忆 ────────────────────────────────────────────
    memory_enabled_default: bool = True
    memory_max_facts: int = Field(default=200, ge=1, le=10000)
    """单个伴侣的记忆条数上限。到顶后淘汰最旧的，防止无限膨胀。"""

    memory_inject_limit: int = Field(default=30, ge=1, le=500)
    """每次注入 system prompt 的记忆条数上限（取最近 N 条）。"""

    # ── 备份 ────────────────────────────────────────────────
    backup_enabled: bool = True
    backup_keep: int = Field(default=7, ge=0, le=100)
    """保留最近几份备份。0 = 不保留（不推荐）。"""

    # ── 校验 ────────────────────────────────────────────────
    @field_validator("admin_usernames")
    @classmethod
    def _strip_admins(cls, v: str) -> str:
        return ",".join(x.strip() for x in v.split(",") if x.strip())

    # ── 派生属性 / 目录 ─────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def admin_set(self) -> set[str]:
        return {x.strip().lower() for x in self.admin_usernames.split(",") if x.strip()}

    def data_root(self) -> Path:
        """数据根目录（容器里建议挂卷到 `/data`）。"""
        return Path(self.data_dir).expanduser().resolve()

    def users_root(self) -> Path:
        """所有用户的数据总目录：`<data>/users/`。"""
        return self.data_root() / "users"

    def user_data_dir(self, user_id: str) -> Path:
        """单个用户的数据目录：`<data>/users/<user_id>/`。

        这是**多用户隔离的物理边界**：一个用户的伴侣、会话、密钥、记忆
        全在自己的目录里，另一个用户即使拿到对方的 companion_id 也读不到文件。
        `user_id` 必须已经过 `core.validation.validate_id()` 校验（无路径分隔符）。
        """
        return self.users_root() / user_id

    def secret_file(self) -> Path:
        """自动生成的根密钥落盘位置（仅非生产环境使用）。"""
        return self.data_root() / ".app_secret"

    def log_dir(self) -> Path:
        return self.data_root() / "logs"

    def backup_dir(self, user_id: str) -> Path:
        return self.user_data_dir(user_id) / "backups"

    # ── 生产环境硬校验 ──────────────────────────────────────
    def validate_for_startup(self) -> list[str]:
        """启动前自检，返回**致命问题**列表（非空则拒绝启动）。

        为什么要 fail fast：弱密钥 / 无口令这类问题如果在生产里静默放过，
        表现是「一切正常但数据可被解密」或「谁都能进来」，事后极难发现。
        """
        problems: list[str] = []
        if not self.is_production:
            return problems

        if not self.app_secret:
            problems.append(
                "生产环境必须设置 SOULMATE_APP_SECRET（否则每次重启密钥都变，已加密的 API Key 将无法解密）"
            )
        elif len(self.app_secret) < 32:
            problems.append("SOULMATE_APP_SECRET 至少 32 个字符（当前 %d）" % len(self.app_secret))
        elif self.app_secret in _WEAK_SECRETS:
            problems.append("SOULMATE_APP_SECRET 是常见弱值，请换一个随机串")

        if self.debug:
            problems.append("生产环境必须关闭 SOULMATE_DEBUG")

        if self.allow_signup and self.auth_mode == "local":
            problems.append(
                "生产环境若使用本机账号且开启自助注册，任何人都能开号。"
                "请设 SOULMATE_ALLOW_SIGNUP=false，或改用 SOULMATE_AUTH_MODE=oidc"
            )
        return problems


# 明显不安全的占位值，出现在生产即拒绝启动
_WEAK_SECRETS = {
    "secret",
    "changeme",
    "change-me",
    "password",
    "soulmate",
    "test",
    "dev",
    "development",
    "0123456789",
    "1234567890",
}


def _bootstrap_secret(settings: Settings) -> str:
    """确保有一个可用的根密钥。

    非生产环境：自动生成并落盘 `<data>/.app_secret`，让 `git clone` 后零配置可跑。
    生产环境：不去猜、不落盘明文，直接返回空串让 `validate_for_startup()` 拦下。
    文件权限设成 0600（仅属主可读写），避免同机其他账号读到。
    """
    if settings.is_production:
        return settings.app_secret

    path = settings.secret_file()
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        import secrets as _secrets

        generated = _secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated, encoding="utf-8")
        try:
            os.chmod(path, 0o600)  # Windows 上基本无效，POSIX 上有效；失败不影响功能
        except OSError:  # pragma: no cover - 平台差异
            pass
        return generated
    except OSError:
        # 磁盘只读等极端情况：退回进程内随机密钥（本次运行有效，重启后已存密钥解不开）
        import secrets as _secrets

        return _secrets.token_urlsafe(48)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。配置在进程生命周期里不变，缓存可避免重复读 `.env`。

    测试里要改配置：`get_settings.cache_clear()` 后重设环境变量，
    或直接用 `Settings(...)` 构造一个独立实例（推荐，无全局副作用）。
    """
    s = Settings()
    if not s.app_secret:
        object.__setattr__(s, "app_secret", _bootstrap_secret(s))
    return s


# 刻意**不**在 import 时实例化：让 `import soulmate.core.settings` 零副作用
# （不读 .env、不写 .app_secret），这样测试才能干净地控制配置。
# 使用方统一 `get_settings()` 获取单例。
