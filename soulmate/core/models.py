"""领域模型：全项目共享的「数据形状」。

【为什么用 pydantic 而不是裸 dict】
裸 dict 有三个坑：① 字段名打错不报错，静默存错；② 类型不校验，
`model` 字段可能装进一个数字；③ 没有默认值，到处 `d.get("x", 0)` 重复。
pydantic 把「校验 + 默认值 + 序列化」集中在一处，字段错立刻抛 `ValidationError`。

【存放边界】
- `Provider.api_key` 在**内存**里是明文，落盘由 storage 层加密后写 `api_key_enc`。
  模型本身不知道加密这回事 —— 那是仓储（repositories）的职责。
- `ChatMessage` 同时被 storage（会话内容）和 llm（发给模型的 messages）用，
  所以放在 core 层而不是 llm 层。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["system", "user", "assistant"]


def _now_iso() -> str:
    """统一的时间戳：ISO8601 + 秒级精度 + 显式时区。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ChatMessage(BaseModel):
    """一条聊天消息。与 OpenAI messages 数组的元素对齐。"""

    model_config = ConfigDict(extra="ignore")

    role: Role
    content: str = ""

    @field_validator("content")
    @classmethod
    def _content_str(cls, v: object) -> str:
        return str(v or "")


class Provider(BaseModel):
    """一个模型服务：Base URL + Key + 模型名。多个伴侣可共用。"""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    preset: str = "custom"
    alias: str = ""
    base_url: str = ""
    api_key: str = ""          # 内存里明文；落盘加密
    model: str = ""
    created_at: str = Field(default_factory=_now_iso)

    @property
    def label(self) -> str:
        """给界面/日志的显示名：别名 > 模型名 > 未命名。"""
        return self.alias or self.model or "（未命名）"


class Companion(BaseModel):
    """一个伴侣：人设 + 绑定哪个模型服务（可配降级）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str = ""
    purpose: str = ""
    avatar: str = "🧸"
    system_prompt: str = ""
    provider_id: str = ""
    fallback_provider_id: str = ""   # 主模型失败时切到它（降级链）
    created_at: str = Field(default_factory=_now_iso)


class SessionMeta(BaseModel):
    """会话的「目录条目」（不载入消息正文），给侧边栏列表用。"""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    title: str = "新会话"
    title_source: Literal["auto", "ai", "manual"] = "auto"
    updated_at: str = ""
    count: int = 0


class SessionDoc(BaseModel):
    """一个会话的完整文档 = 元信息 + 消息列表。落盘单元。"""

    model_config = ConfigDict(extra="ignore")

    session_id: str = ""
    title: str = ""
    title_source: Literal["auto", "ai", "manual"] = "auto"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    messages: list[ChatMessage] = Field(default_factory=list)


class MemoryFact(BaseModel):
    """一条长期记忆（跨会话记住的「关于用户的事实」）。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    created_at: str = Field(default_factory=_now_iso)
    session_id: str = ""


class Profile(BaseModel):
    """使用者自己的资料（每个用户一份，所有伴侣共用）。"""

    model_config = ConfigDict(extra="ignore")

    nickname: str = ""
    avatar: str = "🐶"
    theme: str = "月白"          # 持久化的主题偏好（修旧版「重启回默认」的问题）


class UserRecord(BaseModel):
    """一个账号（认证层用）。password_hash 是 bcrypt 结果，绝不存明文。"""

    model_config = ConfigDict(extra="ignore")

    username: str
    password_hash: str = ""          # 本机账号才需要；OIDC 用户可为空
    role: Literal["admin", "user"] = "user"
    disabled: bool = False
    oidc_sub: str = ""               # OIDC 用户的稳定身份（email/sub），本机账号为空
    display_name: str = ""           # 显示名（OIDC 用户通常是邮箱）
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
