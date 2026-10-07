"""mtime 缓存：同一路径「文件没变就复用上次读的结果」。

【解决什么】
`list_sessions` 之前每次 rerun 都全量读盘（O(n) 次 open+parse）。
Streamlit 是「每次交互都全量重跑」的架构，一个页面点一下按钮，侧边栏
就可能把所有会话文件读一遍 —— 会话一多就卡。

本缓存以 `(mtime_ns, size)` 做失效信号：文件内容变了（mtime 变）或换了文件
（size 变）就重新读，否则返回内存里的旧值。比单纯记「上次读的时间」更可靠。

【边界】
进程内缓存。多进程/多 worker 各自一份，互不可见 —— 单进程 Streamlit 够用。
"""

from __future__ import annotations

import os
from typing import Any, Callable


class MTimeCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[tuple[int, int], Any]] = {}

    def get(self, path: str, loader: Callable[[], Any]) -> Any:
        try:
            st = os.stat(path)
        except OSError:
            # 文件没了（被删/从未存在）→ 走 loader，并清掉可能的旧缓存
            self._store.pop(path, None)
            value = loader()
            return value
        key = (st.st_mtime_ns, st.st_size)
        hit = self._store.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        value = loader()
        self._store[path] = (key, value)
        return value

    def invalidate(self, path: str) -> None:
        self._store.pop(path, None)

    def clear(self) -> None:
        self._store.clear()
