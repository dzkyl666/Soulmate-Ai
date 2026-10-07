"""OpenAI 兼容 Provider：唯一 `import openai` 的文件。

【为什么每次调用新建 client】
- `timeout`（对话 60s vs 抽取 15s）和 `max_retries`（对话 2 次 vs 抽取 0 次）
  在 SDK 里都是**构造时**参数，每次请求的诉求不一样 → 每请求构造一次。
- 构造 client 只是「描述参数」，真正的网络连接发生在 create() 时才建立，
  所以每请求构造没有额外网络开销。
"""

from __future__ import annotations

from typing import Iterator, Sequence

import openai

from soulmate.core.logging import get_logger
from soulmate.core.models import ChatMessage
from soulmate.llm.base import LLMProvider
from soulmate.llm.retry import classify_sdk_error
from soulmate.llm.types import (
    ChunkEvent,
    EndEvent,
    ErrorEvent,
    LLMResult,
    StreamEvent,
    TokenUsage,
    error_event_from,
)

log = get_logger("soulmate.llm.openai")


class OpenAIProvider(LLMProvider):
    def __init__(self, *, api_key: str, base_url: str | None, label: str) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self.label = label

    def _client(self, timeout: float, max_retries: int) -> openai.OpenAI:
        return openai.OpenAI(
            api_key=self._api_key or "sk-empty",  # 空 key 让请求走「未认证」错误路径，而不是构造期报错
            base_url=self._base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    def _messages(self, messages: Sequence[ChatMessage]) -> list[dict]:
        return [m.model_dump(exclude_none=True) for m in messages]

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
        kwargs: dict = {"model": model, "messages": self._messages(messages), "stream": False}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            resp = self._client(timeout, max_retries).chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一翻译成项目异常
            raise classify_sdk_error(exc, model=model, base_url=self._base_url or "") from exc

        usage = TokenUsage()
        if resp.usage is not None:
            usage = TokenUsage(
                prompt_tokens=resp.usage.prompt_tokens or 0,
                completion_tokens=resp.usage.completion_tokens or 0,
                total_tokens=resp.usage.total_tokens or 0,
                model=model,
            )
        return LLMResult(text=(resp.choices[0].message.content or "").strip(), model=model, usage=usage)

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
        kwargs: dict = {"model": model, "messages": self._messages(messages), "stream": True}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        try:
            client = self._client(timeout, max_retries)
        except Exception as exc:  # 构造期异常（如 base_url 非法的 SyntaxError，SDK 是运行时抛）
            yield error_event_from(classify_sdk_error(exc, model=model, base_url=self._base_url or ""), provider_label=self.label)
            return

        buf: list[str] = []
        usage = TokenUsage(model=model)
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            yield error_event_from(classify_sdk_error(exc, model=model, base_url=self._base_url or ""), provider_label=self.label)
            return

        try:
            for chunk in response:
                # 部分厂商在流末尾会多发一个只有用量、没有 choices 的 chunk
                if chunk.usage is not None:
                    usage = TokenUsage(
                        prompt_tokens=chunk.usage.prompt_tokens or 0,
                        completion_tokens=chunk.usage.completion_tokens or 0,
                        total_tokens=chunk.usage.total_tokens or 0,
                        model=model,
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    buf.append(delta)
                    yield ChunkEvent(delta=delta, model=model)
            yield EndEvent(text="".join(buf), model=model, usage=usage)
        except Exception as exc:  # 流中途断开：也按错误事件收尾，绝不裸抛
            log.warning("流式中断: %s", exc)
            yield error_event_from(classify_sdk_error(exc, model=model, base_url=self._base_url or ""), provider_label=self.label)