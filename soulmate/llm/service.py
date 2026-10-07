"""LLMService：模型调用的统一入口（fallback 链在这里）。

【职责】
- `chat()`：一次非流式调用，主模型失败自动切备选模型（降级链）
- `stream()`：流式调用，产 `StreamEvent`；主模型中断自动切备选
- `trim_to_context_budget()`：纯函数，把历史裁到 token 预算内（防烧钱）

【降级链语义】
  主模型 →（可重试类错误）→ 备选模型 →（仍失败）→ ErrorEvent
切换到备选时先产一个 `FallbackEvent`，UI 收到后清掉半截回复、提示用户已切换。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from soulmate.core.exceptions import RateLimitError
from soulmate.core.logging import get_logger
from soulmate.core.models import ChatMessage, Provider
from soulmate.core.ratelimit import get_limiter
from soulmate.core.settings import Settings
from soulmate.llm.registry import ProviderRegistry, get_registry
from soulmate.llm.types import (
    ErrorEvent,
    FallbackEvent,
    LLMResult,
    StreamEvent,
    error_event_from,
)

log = get_logger("soulmate.llm.service")


class LLMService:
    def __init__(
        self,
        settings: Settings,
        user_id: str = "",
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._settings = settings
        # user_id 由 ServiceContainer 在构造时注入（容器本来就持有它，无需贯穿接口）。
        # 它是**限流 key 的一部分**：配额必须按用户隔离，
        # 否则一个人刷满会让所有人被一起锁死。
        self._user_id = user_id
        self._registry = registry or get_registry()

    # ══════════════════════════════════════════════════════════
    # 限流记账
    # ══════════════════════════════════════════════════════════
    @property
    def user_id(self) -> str:
        """本服务归属的用户（限流 key 的一部分）。公开出来便于诊断与测试断言。"""
        return self._user_id

    def _charge_quota(self) -> None:
        """给当前用户的「模型调用」配额记一次账；超限抛 `RateLimitError`。

        为什么 chat() 和 stream() **都要**调：这是两个独立入口，
        只堵 stream() 会漏掉 **AI 起名**（它走 chat()）与记忆抽取。
        """
        get_limiter().hit(
            f"chat:{self._user_id or 'anonymous'}",
            limit=self._settings.rate_limit_chat_per_minute,
            per_seconds=60,
        )

    # ══════════════════════════════════════════════════════════
    # 非流式（AI 起名 / 记忆抽取）
    # ══════════════════════════════════════════════════════════
    def chat(
        self,
        provider_cfg: Provider,
        messages: Sequence[ChatMessage],
        system_prompt: str = "",
        *,
        fallback_cfg: Provider | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> LLMResult:
        # 入口即记账：超限抛 RateLimitError（普通函数，由调用方处理）
        self._charge_quota()

        payload = self._with_system(messages, system_prompt)
        timeout = timeout if timeout is not None else self._settings.request_timeout_seconds
        max_retries = max_retries if max_retries is not None else self._settings.max_retries

        candidates: list[Provider] = [provider_cfg]
        if fallback_cfg is not None:
            candidates.append(fallback_cfg)

        last_err: Exception | None = None
        for i, cfg in enumerate(candidates):
            try:
                return self._registry.build(cfg).chat(
                    payload,
                    model=cfg.model,
                    timeout=timeout,
                    max_retries=max_retries,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                last_err = exc
                if i < len(candidates) - 1:
                    log.warning("主模型失败，切换备选 %s -> %s: %s", cfg.label, candidates[i + 1].label, exc)
                else:
                    log.error("所有模型均失败: %s", exc)
        assert last_err is not None
        raise last_err

    # ══════════════════════════════════════════════════════════
    # 流式（聊天主区）
    # ══════════════════════════════════════════════════════════
    def stream(
        self,
        provider_cfg: Provider,
        messages: Sequence[ChatMessage],
        system_prompt: str = "",
        *,
        fallback_cfg: Provider | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        """流式入口：先记账，再委托给 `_stream_impl`。

        【与 chat() 的「不对称」是刻意的，不是疏漏】
        - `chat()` 是普通函数：超限直接抛 `RateLimitError`，调用方自己处理；
        - `stream()` 超限**不抛异常**，而是产一个 `ErrorEvent` 收尾。

        原因是本层的契约：**事件流总是有始有终**，UI 才能线性 `for` 消费、
        不需要 try 包生成器。若这里抛异常，就会破坏这个契约，
        并且异常会从 UI 的 for 循环里冒出去、变成整页红框。

        记账写在**调用时**（而非等第一次迭代）：调用即计费，语义更直观。
        """
        try:
            self._charge_quota()
        except RateLimitError as exc:
            log.warning("聊天限流触发：%s", exc)
            return iter(
                [
                    ErrorEvent(
                        user_message=exc.user_message(),
                        retryable=True,
                        provider_label="限流",
                    )
                ]
            )
        return self._stream_impl(
            provider_cfg,
            messages,
            system_prompt,
            fallback_cfg=fallback_cfg,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _stream_impl(
        self,
        provider_cfg: Provider,
        messages: Sequence[ChatMessage],
        system_prompt: str = "",
        *,
        fallback_cfg: Provider | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        """真正的流式实现（含降级链）。限流已在 `stream()` 里记过账。"""
        payload = self._with_system(messages, system_prompt)
        candidates: list[Provider] = [provider_cfg] + ([fallback_cfg] if fallback_cfg else [])
        last_error: ErrorEvent | None = None

        for i, cfg in enumerate(candidates):
            provider = self._registry.build(cfg)
            try:
                for evt in provider.stream(
                    payload,
                    model=cfg.model,
                    timeout=self._settings.request_timeout_seconds,
                    max_retries=0,  # 重试语义由降级链负责（SDK 内部重试会拖慢切备选的时机）
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if isinstance(evt, ErrorEvent):
                        last_error = evt
                        if i < len(candidates) - 1:
                            # 主模型挂了，先告诉 UI 要切，UI 清掉半截再继续
                            yield FallbackEvent(
                                from_label=cfg.label,
                                to_label=candidates[i + 1].label,
                            )
                        break
                    yield evt
                else:
                    # for 正常走完 = 某个 provider 成功到底
                    return
            except Exception as exc:  # provider 违约抛异常（不应发生，防御性兜底）
                last_error = error_event_from(self._as_provider_error(exc), provider_label=cfg.label)
                if i < len(candidates) - 1:
                    yield FallbackEvent(from_label=cfg.label, to_label=candidates[i + 1].label)
                else:
                    yield last_error
                    return
            except GeneratorExit:
                # UI 中断（用户切走/关页）：直接结束，不补错误事件
                return

        # 全部 candidate 都失败
        if last_error is not None:
            yield last_error

    # ══════════════════════════════════════════════════════════
    # 工具方法
    # ══════════════════════════════════════════════════════════
    @staticmethod
    def _with_system(messages: Sequence[ChatMessage], system_prompt: str) -> list[ChatMessage]:
        """system 永远排最前（格式规则只存在于这一处）。"""
        if not system_prompt:
            return list(messages)
        return [ChatMessage(role="system", content=system_prompt), *messages]

    @staticmethod
    def trim_to_context_budget(messages: Sequence[ChatMessage], max_chars: int) -> list[ChatMessage]:
        """把历史裁到预算内：从最旧开始丢，保住最近的对话。

        中文一个字约占 1 token 左右，直接用字符数做预算足够粗准；
        要更精确就按 tokenizer 算，但那得引入额外依赖，收益不大。
        """
        if max_chars <= 0:
            return []
        total, kept = 0, []
        for m in reversed(messages):
            cost = len(m.content) + 8  # 每条消息的固定开销（role/分隔符）
            if total + cost > max_chars:
                break
            kept.append(m)
            total += cost
        kept.reverse()
        return kept

    def _as_provider_error(self, exc: Exception):  # pragma: no cover - 防御路径
        from soulmate.core.exceptions import ProviderError

        if isinstance(exc, ProviderError):
            return exc
        return ProviderError(str(exc), user_message="模型调用失败，请稍后重试", retryable=False)
