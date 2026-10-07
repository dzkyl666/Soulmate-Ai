"""llm 层测试：降级链 / 流式事件 / 上下文预算 / 错误分类 / 退避。"""

from __future__ import annotations

import openai
import pytest

try:  # openai >=3 用的是 httpx2；更早版本用 httpx。两者 API 一致。
    import httpx2 as httpx
except ImportError:  # pragma: no cover - 兼容旧版 openai
    import httpx  # type: ignore[no-redef]

from soulmate.core.exceptions import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderTimeout,
)
from soulmate.core.models import ChatMessage, Provider
from soulmate.llm.retry import backoff_delay, classify_sdk_error
from soulmate.llm.service import LLMService
from soulmate.llm.types import EndEvent, ErrorEvent, FallbackEvent
from tests.fakes import FakeProvider

MSG = [ChatMessage(role="user", content="hi")]


def _svc(settings, registry) -> LLMService:
    return LLMService(settings, registry=registry)


def _p(pid: str, model: str = "m") -> Provider:
    return Provider(id=pid, model=model, base_url="https://x.com/v1", api_key="k")


# ══════════════════════════════════════════════════════════
# 非流式降级
# ══════════════════════════════════════════════════════════
class TestChatFallback:
    def test_primary_used_when_healthy(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主"))
        svc = _svc(settings, fake_registry)
        assert svc.chat(_p("p1"), MSG).text == "[主] hi"

    def test_falls_back_when_primary_fails(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主", fail=True))
        fake_registry.register("p2", FakeProvider("备"))
        svc = _svc(settings, fake_registry)
        assert svc.chat(_p("p1"), MSG, fallback_cfg=_p("p2")).text == "[备] hi"

    def test_raises_when_all_fail(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主", fail=True))
        fake_registry.register("p2", FakeProvider("备", fail=True))
        svc = _svc(settings, fake_registry)
        with pytest.raises(Exception):
            svc.chat(_p("p1"), MSG, fallback_cfg=_p("p2"))

    def test_raises_without_fallback(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主", fail=True))
        svc = _svc(settings, fake_registry)
        with pytest.raises(Exception):
            svc.chat(_p("p1"), MSG)

    def test_system_prompt_goes_first(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主", reply="{last}"))
        svc = _svc(settings, fake_registry)
        assert svc.chat(_p("p1"), MSG, system_prompt="你是系统").text == "hi"

    def test_timeout_and_retries_are_forwarded(self, settings, fake_registry):
        spy = FakeProvider("主")
        fake_registry.register("p1", spy)
        svc = _svc(settings, fake_registry)
        svc.chat(_p("p1"), MSG, timeout=7.5, max_retries=0)
        assert spy.calls[0]["timeout"] == 7.5 and spy.calls[0]["max_retries"] == 0


# ══════════════════════════════════════════════════════════
# 流式 + 降级
# ══════════════════════════════════════════════════════════
class TestStreamFallback:
    def test_streams_chunks_then_end(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主"))
        svc = _svc(settings, fake_registry)
        events = list(svc.stream(_p("p1"), MSG))
        assert [e.kind for e in events] == ["chunk", "end"]
        assert events[-1].text == "[主] hi"
        assert events[-1].usage.total_tokens == 15

    def test_partial_then_fallback_emits_fallback_event(self, settings, fake_registry):
        """主模型中途断掉 → UI 先收到 chunk，再收到 fallback，最后由备选跑完。"""
        fake_registry.register("p1", FakeProvider("主", fail=True, partial=True))
        fake_registry.register("p2", FakeProvider("备"))
        svc = _svc(settings, fake_registry)
        kinds = [e.kind for e in svc.stream(_p("p1"), MSG, fallback_cfg=_p("p2"))]
        assert kinds[0] == "chunk"
        assert "fallback" in kinds
        assert kinds[-1] == "end"

    def test_all_fail_ends_with_error_event(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主", fail=True))
        svc = _svc(settings, fake_registry)
        events = list(svc.stream(_p("p1"), MSG, fallback_cfg=_p("p1")))
        assert isinstance(events[0], FallbackEvent)
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].user_message  # 有给人看的话

    def test_no_fallback_failure_yields_error_only(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主", fail=True))
        svc = _svc(settings, fake_registry)
        events = list(svc.stream(_p("p1"), MSG))
        assert len(events) == 1 and isinstance(events[0], ErrorEvent)

    def test_provider_raising_unexpectedly_is_contained(self, settings, fake_registry):
        """provider 违约抛异常也不能把 UI 带崩 —— 必须转成 ErrorEvent。"""
        fake_registry.register("p1", FakeProvider("主", raise_exc=RuntimeError("boom")))
        fake_registry.register("p2", FakeProvider("备"))
        svc = _svc(settings, fake_registry)
        events = list(svc.stream(_p("p1"), MSG, fallback_cfg=_p("p2")))
        assert events[-1].kind == "end"  # 成功切到备选

    def test_stream_events_are_typed(self, settings, fake_registry):
        fake_registry.register("p1", FakeProvider("主"))
        svc = _svc(settings, fake_registry)
        for e in svc.stream(_p("p1"), MSG):
            assert isinstance(e, (EndEvent, ErrorEvent, FallbackEvent)) or e.kind == "chunk"


# ══════════════════════════════════════════════════════════
# 上下文预算
# ══════════════════════════════════════════════════════════
class TestTrim:
    def test_keeps_most_recent_within_budget(self):
        msgs = [
            ChatMessage(role="user", content="x" * 1000),
            ChatMessage(role="assistant", content="y" * 1000),
            ChatMessage(role="user", content="z"),
        ]
        assert [m.content for m in LLMService.trim_to_context_budget(msgs, 50)] == ["z"]

    def test_keeps_all_when_under_budget(self):
        msgs = [ChatMessage(role="user", content="a"), ChatMessage(role="assistant", content="b")]
        assert len(LLMService.trim_to_context_budget(msgs, 10_000)) == 2

    def test_zero_budget_returns_empty(self):
        assert LLMService.trim_to_context_budget([ChatMessage(role="user", content="a")], 0) == []


# ══════════════════════════════════════════════════════════
# 错误分类
# ══════════════════════════════════════════════════════════
def _http_response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://api.example.com/v1"))


class TestClassify:
    def test_authentication_error(self):
        exc = openai.AuthenticationError("bad key", response=_http_response(401), body=None)
        err = classify_sdk_error(exc, model="m", base_url="https://x")
        assert isinstance(err, ProviderAuthError)
        assert err.retryable is False
        assert "API Key" in err.user_message()

    def test_rate_limit(self):
        exc = openai.RateLimitError("429", response=_http_response(429), body=None)
        assert isinstance(classify_sdk_error(exc), ProviderRateLimited)

    def test_timeout(self):
        assert isinstance(classify_sdk_error(openai.APITimeoutError(request=httpx.Request("POST", "https://x"))), ProviderBadResponse)

    def test_connection_error_is_retryable(self):
        exc = openai.APIConnectionError(request=httpx.Request("POST", "https://x"))
        err = classify_sdk_error(exc)
        assert err.retryable is True

    def test_5xx_retryable_4xx_not(self):
        assert classify_sdk_error(openai.InternalServerError("500", response=_http_response(500), body=None)).retryable is True
        assert classify_sdk_error(openai.BadRequestError("400", response=_http_response(400), body=None)).retryable is False

    def test_unknown_exception_becomes_provider_error(self):
        err = classify_sdk_error(ValueError("???"))
        assert err.retryable is True and err.user_message()

    def test_user_message_never_leaks_key(self):
        exc = openai.AuthenticationError("key sk-abcdefghijklmnop rejected", response=_http_response(401), body=None)
        assert "sk-abcdefghijklmnop" not in classify_sdk_error(exc).user_message()


class TestBackoff:
    def test_grows_exponentially_with_jitter(self):
        base = 0.5
        d0, d1, d2 = (backoff_delay(i, base) for i in range(3))
        assert base <= d0 < base * 2 + base
        assert d1 > d0 - base  # 单调趋势（含抖动，用宽松下界）
        assert d2 > d1 - base

    def test_never_negative(self):
        for i in range(6):
            assert backoff_delay(i, 0.1) >= 0