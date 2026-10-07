"""Provider 抽象：上层只依赖这个接口，换厂商 = 加一个实现类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence

from soulmate.core.models import ChatMessage
from soulmate.llm.types import LLMResult, StreamEvent


class LLMProvider(ABC):
    """一个「OpenAI 兼容」模型端点的最小能力面。

    实现要点：
    - `chat()` 非流式，返回完整结果，失败抛 `ProviderError` 子类；
    - `stream()` 流式，**绝不抛异常** —— 失败一律发 `ErrorEvent` 自行结束，
      这样上层（降级链 / UI）不需要 try 流生成器，事件流总是「有始有终」。
    """

    #: 给日志/降级提示用的显示名
    label: str = "unknown"

    @abstractmethod
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        timeout: float,
        max_retries: int,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult: ...

    @abstractmethod
    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        timeout: float,
        max_retries: int,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]: ...
