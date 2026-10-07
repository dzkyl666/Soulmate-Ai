"""测试替身：假 Provider / 假注册表。

【为什么要可编程的假 Provider】
降级链、流式中断、错误分类这些逻辑，用真实 API 根本没法稳定复现
（你怎么让线上模型「恰好在中途断掉」？）。
所以用一个能「按脚本演出失败」的假 Provider，把每条分支都固定下来。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from soulmate.core.exceptions import ProviderError
from soulmate.core.models import ChatMessage, Provider
from soulmate.llm.base import LLMProvider
from soulmate.llm.types import (
    ChunkEvent,
    EndEvent,
    LLMResult,
    StreamEvent,
    TokenUsage,
    error_event_from,
)


class FakeProvider(LLMProvider):
    """按脚本演出的假模型。

    参数：
        label    显示名
        fail     是否失败
        partial  失败前是否先吐一段（模拟"流中途断掉"）
        retryable 失败是否标记为可重试
        reply    成功时返回的文本模板（{label} 会被替换）
        raise_exc 失败时抛出的异常（用于测「provider 违约抛异常」的兜底分支）
    """

    def __init__(
        self,
        label: str,
        *,
        fail: bool = False,
        partial: bool = False,
        retryable: bool = True,
        reply: str = "[{label}] {last}",
        raise_exc: BaseException | None = None,
        calls: list | None = None,
    ) -> None:
        self.label = label
        self.fail = fail
        self.partial = partial
        self.retryable = retryable
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = calls if calls is not None else []

    # ── 非流式 ──
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        timeout: float,
        max_retries: int,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        self.calls.append({"model": model, "timeout": timeout, "max_retries": max_retries})
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.fail:
            raise ProviderError(f"{self.label} 失败", retryable=self.retryable)
        last = messages[-1].content if messages else ""
        return LLMResult(
            text=self.reply.format(label=self.label, last=last),
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, model=model),
        )

    # ── 流式 ──
    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        timeout: float,
        max_retries: int,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        self.calls.append({"model": model, "stream": True})
        if self.partial:
            yield ChunkEvent(delta=f"[{self.label}半截]", model=model)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.fail:
            err = ProviderError(f"{self.label} 流失败", retryable=self.retryable)
            yield error_event_from(err, provider_label=self.label)
            return
        text = self.reply.format(label=self.label, last=messages[-1].content if messages else "")
        yield ChunkEvent(delta=text, model=model)
        yield EndEvent(text=text, model=model, usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, model=model))


class FakeRegistry:
    """按 provider.id 查表的假注册表。"""

    def __init__(self) -> None:
        self.providers: dict[str, LLMProvider] = {}

    def register(self, provider_id: str, provider: LLMProvider) -> FakeRegistry:
        self.providers[provider_id] = provider
        return self

    def build(self, cfg: Provider) -> LLMProvider:
        if cfg.id not in self.providers:
            raise AssertionError(f"假注册表里没有 provider {cfg.id}（忘了 register？）")
        return self.providers[cfg.id]
