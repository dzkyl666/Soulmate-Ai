"""llm 层共享的类型：调用结果 + 流式事件协议。

【流式事件协议】provider.stream() 只产这类事件，UI 按 `kind` 分流处理：
    chunk    → 追加文本
    fallback → 主模型失败，已切换备选（UI 可清掉半截回复重画）
    error    → 失败（带安全文案 + 是否可重试）
    end      → 正常结束（带用量）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from soulmate.core.exceptions import ProviderError
from soulmate.core.models import ChatMessage

__all__ = ["ChatMessage", "TokenUsage", "LLMResult", "ChunkEvent", "FallbackEvent", "ErrorEvent", "EndEvent"]


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""


class LLMResult(BaseModel):
    """一次非流式调用的结果。"""

    text: str = ""
    model: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)


# ── 流式事件 ──
class ChunkEvent(BaseModel):
    kind: Literal["chunk"] = "chunk"
    delta: str = ""
    model: str = ""


class FallbackEvent(BaseModel):
    kind: Literal["fallback"] = "fallback"
    from_label: str = ""
    to_label: str = ""


class ErrorEvent(BaseModel):
    kind: Literal["error"] = "error"
    user_message: str = "模型调用失败，请稍后重试"
    retryable: bool = False
    provider_label: str = ""


class EndEvent(BaseModel):
    kind: Literal["end"] = "end"
    text: str = ""
    model: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)


StreamEvent = ChunkEvent | FallbackEvent | ErrorEvent | EndEvent


def error_event_from(error: ProviderError, *, provider_label: str = "") -> ErrorEvent:
    """把 `ProviderError` 翻译成 UI 能直接展示的事件。"""
    return ErrorEvent(
        user_message=error.user_message(),
        retryable=error.retryable,
        provider_label=provider_label,
    )