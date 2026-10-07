"""UI 边界的统一异常处理。

【策略：边界只捕根异常 `SoulmateError`，不逐个捕具体子类】

本项目所有业务异常都继承 `SoulmateError`，且每个都实现了 `user_message()`
（一句给用户看的安全文案，不含内部路径 / Base URL / API Key）。

所以在 UI 边界上只需要一条规则：

    except SoulmateError → 显示 user_message()

【为什么不能逐个捕 —— 一个已经付过代价的教训】

`RateLimitError` 是 `SoulmateError` 的**直接子类**，不是 `AuthError` 的子类。
而登录页原先写的是 `except AuthError`，于是：

    用户连点登录 → 触发登录限流 → RateLimitError 不被接住
    → 异常冒到 Streamlit → **整页红框**（而不是提示"操作太频繁"）

同类隐患还有：`providers.upsert()` 会写盘、可能抛 `StorageError`，
而弹窗只捕 `ValidationError` —— 磁盘满时同样是整页崩。

结论：**新增一种异常就等于新增一个"页面崩溃"入口**，除非边界捕根异常。
把策略收敛到本模块一处，避免第 N 个边界又被写窄。

【已知的、有意为之的例外】

- `ui/chat.py` 的**记忆抽取**：限流只记日志、不弹提示 ——
  抽取是锦上添花，回复已经落盘并显示给用户了，再弹错会误导。
- `ui/chat.py` 的 **AI 起名**：按异常类型分级（限流用 warning，其余用 error）——
  限流是"稍等"而不是"出错"，提示严重度不同。
  该处另有 `except Exception` 兜底，不存在崩溃缺口。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import streamlit as st

from soulmate.core.exceptions import SoulmateError
from soulmate.core.logging import get_logger

log = get_logger("soulmate.ui.boundary")

_T = TypeVar("_T")


def guard(action: Callable[[], _T], *, what: str) -> _T | None:
    """执行一个可能抛项目异常的动作：成功返回结果，失败提示并返回 None。

    `what` 只用于日志（便于运维按动作检索），不进界面。
    """
    try:
        return action()
    except SoulmateError as exc:
        log.info("%s 被拒: code=%s", what, exc.code)
        st.error(exc.user_message())
        return None
