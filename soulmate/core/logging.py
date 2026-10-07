"""结构化日志：全项目唯一入口。

【为什么必须有这一层】
重构前全项目 0 个 logger，出错只能靠 Streamlit 界面那一行红字，刷新就没了。
生产上这是「盲飞」：用户报了 bug，你连一条日志都拿不出来。

本模块提供：
1. `setup_logging(settings)` —— 进程启动时调用一次，配好 console + 滚动文件两个 handler
2. `get_logger(name)` —— 各模块拿 logger（等价于 logging.getLogger，但保证已初始化）
3. `request_id` —— 用 contextvar 给「同一次页面渲染」打上同一 ID，
   多用户同时操作时，靠它把同一请求散落的多条日志串起来
4. `log_exception` —— 统一记录 `SoulmateError` 的结构化字段，且**先脱敏**再落盘
"""

from __future__ import annotations

import contextvars
import logging
import logging.handlers
import sys
import uuid
from typing import Any

from soulmate.core.exceptions import SoulmateError, redact

# 同一次请求（同一次 Streamlit rerun）共享的追踪 ID
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_configured = False


def setup_logging(log_level: int = logging.INFO, log_dir: str = "") -> None:
    """初始化根 logger。幂等：重复调用不会叠加 handler。

    参数：
        log_level: 根级别。debug 时降到 DEBUG。
        log_dir: 滚动日志文件目录；空串则不写文件（测试环境）。
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(log_level)

    # 控制台：人读的，带上 request_id 前缀
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] [req=%(request_id)s] %(message)s")
    )
    root.addHandler(console)

    if log_dir:
        import os

        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "soulmate.log"),
            maxBytes=5 * 1024 * 1024,   # 5MB 一个文件
            backupCount=5,              # 最多保留 5 个历史文件
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] [req=%(request_id)s] %(message)s")
        )
        root.addHandler(file_handler)

    _configured = True


# 让 format 里的 `%(request_id)s` 能从 contextvar 取到值
class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def get_logger(name: str) -> logging.Logger:
    """拿一个 logger，并确保根 logger 已初始化（幂等）。

    加 filter 让每个 record 都带上 request_id。
    """
    if not _configured:
        setup_logging()
    logger = logging.getLogger(name)
    if not any(isinstance(f, _RequestIdFilter) for f in logger.filters):
        logger.addFilter(_RequestIdFilter())
    return logger


def set_request_id() -> str:
    """开启一个新的请求追踪 ID 并返回。Streamlit 每次 rerun 顶部调用一次。"""
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def log_exception(logger: logging.Logger, exc: BaseException, *, ctx: dict[str, Any] | None = None) -> None:
    """统一记录异常。SoulmateError 带结构化 details；其它异常兜底。

    关键点：任何写入日志的字符串都先 `redact()`，防止 Key / Bearer 头漏进日志文件。
    """
    if isinstance(exc, SoulmateError):
        payload = exc.to_dict()
        payload["user_message"] = redact(exc.user_message())
        logger.error(
            "业务异常 code=%s msg=%s details=%s",
            exc.code,
            redact(exc.message),
            payload.get("details") or {},
            extra={"ctx": ctx or {}},
        )
    else:
        logger.exception("未预期异常 type=%s msg=%s", type(exc).__name__, redact(str(exc)), extra={"ctx": ctx or {}})
