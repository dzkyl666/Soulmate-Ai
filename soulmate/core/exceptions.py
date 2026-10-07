"""异常体系：一次定义，全项目复用。

【为什么要有自己的异常层，而不是到处 `raise ValueError`】
1. **调用方能不能分级处理**：值错（用户改配置）和密钥错（要重新登录）该有不同反应。
   `except SoulmateError as e: e.code` 就能按码分流，不必去解析错误文本。
2. **给用户看的话和给日志看的话必须分开**：
   `str(e)` 是给日志的（含技术细节），`user_message()` 是给界面的（不含内部路径、URL、Key）。
   之前直接把 `f"❌ 与 AI 通信发生错误: {e}"` 打到界面，会把 Base URL、甚至请求头回显出来。
3. **可以带结构化上下文**：`details` 里挂 provider、model、status_code，
   日志和诊断页都能直接用，不用再从字符串里正则抠。

【分层对应】
    SoulmateError                 所有项目异常的根
    ├─ ConfigError                配置/启动期问题（fail fast）
    ├─ ValidationError            用户输入不合法（可安全展示）
    ├─ AuthError                  未登录/凭据错
    │   ├─ InvalidCredentials
    │   └─ RegistrationDisabled
    ├─ RateLimitError             触发限流（带 retry_after）
    ├─ ProviderError              模型服务侧问题
    │   ├─ ProviderAuthError      Key 无效/欠费（401/403）
    │   ├─ ProviderTimeout        超时
    │   ├─ ProviderRateLimited    429
    │   └─ ProviderBadResponse    4xx/5xx/流中断
    └─ StorageError               落盘问题（磁盘满、权限、JSON 损坏）
"""

from __future__ import annotations

import re
from typing import Any


class SoulmateError(Exception):
    """项目内所有异常的根类。业务代码请捕获这个，而不是裸 `Exception`。"""

    #: 稳定错误码，供界面/日志/监控按码分流（不用解析文案）
    code: str = "soulmate_error"
    #: 给用户看的安全文案。子类可覆盖，或由 `user_message()` 回落到它。
    default_message: str = "操作失败，请稍后重试"

    def __init__(
        self,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
        user_message: str | None = None,
    ) -> None:
        # message 是给日志的技术描述；user_message 是给界面的安全文案
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.details: dict[str, Any] = details or {}
        self._user_message = user_message

    def user_message(self) -> str:
        """给界面显示的文案：绝不包含内部路径、Base URL、API Key。"""
        if self._user_message:
            return self._user_message
        # 兜底：把技术描述里可能带的敏感串洗一遍
        return redact(str(self))

    def to_dict(self) -> dict[str, Any]:
        """结构化输出，给日志 / 诊断页 / 未来的监控上报用。"""
        return {"code": self.code, "message": self.message, "details": dict(self.details)}

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        if self.details:
            return f"[{self.code}] {self.message} | {self.details}"
        return f"[{self.code}] {self.message}"


# ── 配置 / 启动 ────────────────────────────────────────────────
class ConfigError(SoulmateError):
    code = "config_error"
    default_message = "应用配置有误，请检查环境变量"


# ── 输入校验 ───────────────────────────────────────────────────
class ValidationError(SoulmateError):
    code = "validation_error"
    default_message = "输入不合法"


# ── 认证 / 授权 ────────────────────────────────────────────────
class AuthError(SoulmateError):
    code = "auth_error"
    default_message = "请先登录"


class InvalidCredentials(AuthError):
    code = "invalid_credentials"
    default_message = "用户名或密码不正确"


class RegistrationDisabled(AuthError):
    code = "registration_disabled"
    default_message = "当前未开放注册，请联系管理员开号"


class AccountDisabled(AuthError):
    code = "account_disabled"
    default_message = "该账号已被停用"


class PermissionDenied(AuthError):
    code = "permission_denied"
    default_message = "没有权限执行该操作"


# ── 限流 ───────────────────────────────────────────────────────
class RateLimitError(SoulmateError):
    code = "rate_limited"
    default_message = "操作太频繁，请稍后再试"

    def __init__(self, message: str = "", *, retry_after: float = 1.0, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = max(0.0, float(retry_after))

    def user_message(self) -> str:
        return f"操作太频繁了，请等 {self.retry_after:.0f} 秒后再试"


# ── 模型服务 ───────────────────────────────────────────────────
class ProviderError(SoulmateError):
    code = "provider_error"
    default_message = "模型服务调用失败，请稍后重试"

    #: 是否值得重试（服务端瞬时问题）。类级默认 + 实例级可覆盖。
    retryable: bool = False

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        if retryable is not None:
            self.retryable = retryable
        # details 里统一带 retryable，方便日志/诊断直接看
        self.details.setdefault("retryable", self.retryable)


class ProviderAuthError(ProviderError):
    code = "provider_auth_error"
    # 这条要明确指路，否则用户只会看到「失败」而不知道去改 Key。
    #
    # ⚠️ 文案刻意**不**说「是否欠费」：实测过一次误导 —— 真实原因是
    # provider 的 Key 为空且依赖环境变量兜底（v2 曾丢失该功能），
    # 但旧文案把人引向「换 Key / 充值」，方向完全错了。
    default_message = (
        "模型服务拒绝了密钥。请依次确认：① API Key 是否填对；"
        "② 若该模型服务的 Key 留空、依赖环境变量（如 SILICONFLOW_API_KEY），"
        "确认该变量已设置**且服务进程能读到**（环境变量必须在启动服务前设好，"
        "改完需重启）；③ 账户余额/权限是否正常"
    )
    retryable = False


class ProviderTimeout(ProviderError):
    code = "provider_timeout"
    default_message = "模型服务响应超时，请重试或换一个更快的模型"
    retryable = True


class ProviderRateLimited(ProviderError):
    code = "provider_rate_limited"
    default_message = "模型服务限流了，请稍后再试"
    retryable = True


class ProviderBadResponse(ProviderError):
    code = "provider_bad_response"
    default_message = "模型服务返回了异常响应"


class ProviderConfigError(ProviderError):
    code = "provider_config_error"
    default_message = "模型服务配置不完整（地址或模型名为空）"


# ── 存储 ───────────────────────────────────────────────────────
class StorageError(SoulmateError):
    code = "storage_error"
    default_message = "数据读写失败"


class DataCorruptedError(StorageError):
    code = "data_corrupted"
    default_message = "数据文件损坏，已跳过该文件"


# ══════════════════════════════════════════════════════════════
# 敏感信息脱敏
# ══════════════════════════════════════════════════════════════
# 目标：任何要写进日志或打到界面的字符串，都必须先过这一层。
# 覆盖 OpenAI 风格的 sk-xxx、Bearer 头、以及 URL 里的 user:pass@host。
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # sk-xxxx / sk-proj-xxxx / ghp_xxx / AKIAxxx 等常见 Key 形态
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]{4,}"), r"\1…<redacted>"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{4})[A-Za-z0-9]{4,}"), r"\1…<redacted>"),
    (re.compile(r"\b(AKIA[0-9A-Z]{4})[0-9A-Z]{12}\b"), r"\1…<redacted>"),
    # Authorization: Bearer xxx
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/=]{8,}"), r"\1<redacted>"),
    # base_url 里的凭据 https://user:pass@host
    (re.compile(r"(?i)(https?://[^/\s:@]+:)[^/\s@]+(@)"), r"\1<redacted>\2"),
    # 常见字段名后面跟的长串：api_key=xxx / token: xxx
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b(\s*[=:]\s*)([^\s,;\"']{6,})"),
        r"\1\2<redacted>",
    ),
)


def redact(text: str, *, keep: int = 0) -> str:
    """把字符串里的密钥类内容替换成 `<redacted>`。

    用在「异常文案 → 界面/日志」这条唯一出口上，属于纵深防御：
    即使某处忘了脱敏，经过这里也不会原样泄露。
    """
    if not text:
        return text
    out = str(text)
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def mask_secret(value: str, *, visible_tail: int = 4) -> str:
    """给界面回显用：只露尾部几位。`sk-abcdefgh1234` → `••••••1234`。

    为什么要露尾巴：用户需要靠它区分「这是哪一把 Key」，但不需要看到全文。
    """
    if not value:
        return ""
    tail = value[-visible_tail:] if len(value) > visible_tail else ""
    return "•" * 8 + tail
