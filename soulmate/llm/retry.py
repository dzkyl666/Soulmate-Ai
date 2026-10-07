"""SDK 错误分类 + 退避计算。

【为什么单独成模块】
1. 把 openai 的异常翻译成项目自己的 `ProviderError` 子类（401→提示检查 Key、
   429→提示限流、超时→提示换模型…），界面才能按码分流显示人话；
2. 退避延迟集中一处，将来接 Redis / 多 worker 时好统一替换。
"""

from __future__ import annotations

import random
import time
from typing import Any

import openai

from soulmate.core.exceptions import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
)
from soulmate.core.logging import get_logger

log = get_logger("soulmate.llm.retry")


def classify_sdk_error(exc: BaseException, *, model: str = "", base_url: str = "") -> ProviderError:
    """把 openai SDK 抛出的异常翻译成项目自己的异常。

    注意判断顺序：AuthenticationError / PermissionDeniedError / RateLimitError
    都是 APIStatusError 的子类，必须先判断更具体的，最后才兜底 APIStatusError。
    """
    details: dict[str, Any] = {"model": model, "base_url": base_url}

    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ProviderAuthError(
            f"模型服务拒绝了密钥 (model={model})",
            details=details,
            user_message="模型服务拒绝了 API Key：请检查 Key 是否正确、是否欠费",
        )
    if isinstance(exc, openai.RateLimitError):
        return ProviderRateLimited(
            f"模型服务限流 (model={model})",
            details=details,
            user_message="模型服务限流了，请稍后再试或降低对话频率",
        )
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return ProviderBadResponse(
            f"与模型服务通信失败 (model={model})",
            details=details,
            user_message="连接模型服务超时或中断，请检查网络后重试",
            retryable=True,
        )
    if isinstance(exc, openai.BadRequestError):
        return ProviderBadResponse(
            f"模型服务拒绝了请求 (model={model})",
            details=details,
            user_message="请求参数不被模型接受，请检查上下文长度或提示词",
        )
    if isinstance(exc, openai.APIStatusError):
        return ProviderBadResponse(
            f"模型服务返回异常状态 (model={model}, status={getattr(exc, 'status_code', '?')})",
            details=details,
            user_message="模型服务返回异常，请稍后重试",
            retryable=getattr(exc, "status_code", 500) >= 500,
        )
    return ProviderError(
        f"未预期的模型服务异常 ({type(exc).__name__}: {exc})",
        details={**details, "exc_type": type(exc).__name__},
        user_message="模型服务调用失败，请稍后重试",
        retryable=True,
    )


def backoff_delay(attempt: int, base: float) -> float:
    """指数退避 + 随机抖动：`base * 2**attempt + [0, base)`。

    固定延迟的坑：多个请求同时失败后同时重试，会「惊群」地再撞一次。
    加随机抖动把重试时间打散。
    """
    return base * (2 ** attempt) + random.uniform(0, base)


def sleep_backoff(attempt: int, base: float) -> None:
    """睡一个退避周期（attempt 从 0 开始）。便于调用方一行完成。"""
    time.sleep(backoff_delay(attempt, base))