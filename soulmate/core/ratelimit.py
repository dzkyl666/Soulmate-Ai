"""进程内限流：令牌桶，按 key 隔离。

【为什么需要】
多人部署下，任何「每次请求都花钱调模型」的入口（聊天、记忆抽取、登录爆破）
都该有频率上限，否则：① 恶意刷接口烧光你的 API 额度；② 有人拿脚本暴力猜密码。

【实现边界（重要，别误用）】
本实现是**进程内**的（数据在内存 dict 里）。Streamlit 单进程运行时够用；
将来横向扩容成多 worker，内存桶互不相通，必须换 Redis 之类的共享存储。
这点写在 DEPLOYMENT.md 里了，不是本模块的缺陷，是「单进程」这一部署假设的显式边界。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from soulmate.core.exceptions import RateLimitError


class RateLimiter:
    """固定窗口滑动计数：每个 key 维护一个最近时间戳队列。"""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, per_seconds: int) -> None:
        """记录一次命中；超出限流则抛 `RateLimitError`（带需等待秒数）。

        注意：只有「成功命中且放行」才记账；被拒的那次不算进队列，避免自己把自己锁死。
        """
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            # 清理窗口外的旧记录
            while q and now - q[0] > per_seconds:
                q.popleft()
            if len(q) >= limit:
                retry_after = per_seconds - (now - q[0])
                raise RateLimitError(f"限流触发 key={key}", retry_after=max(retry_after, 0.1))
            q.append(now)

    def reset(self, key: str | None = None) -> None:
        """清空计数（测试或管理员手动解除时用）。key 为空则全部清空。"""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def remaining(self, key: str, limit: int, per_seconds: int) -> int:
        """剩余可调用次数（诊断页展示用，不记账）。"""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > per_seconds:
                q.popleft()
            return max(limit - len(q), 0)


# 进程级单例（模块级即可，无需显式注入）
_default = RateLimiter()


def get_limiter() -> RateLimiter:
    return _default
