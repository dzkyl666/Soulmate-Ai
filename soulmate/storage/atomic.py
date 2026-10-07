"""原子 JSON 读写。

【为什么必须原子写】
旧实现用固定 `path + ".tmp"` 做中转，两个致命问题：
1. **并发竞态**：两个请求同时写同一文件，都往同一个 `.tmp` 里灌，
   后写的 `os.replace` 会把前一个刚写完的也覆盖掉，数据互相踩踏。
2. **崩溃残留**：写一半进程被杀，`.tmp` 留在磁盘；虽然读的时候会跳过它，
   但累积多了是垃圾，而且下次写还会继续踩同一个名字。

本实现用 `tempfile.mkstemp` 生成**唯一**临时文件（同目录，保证同卷，`os.replace` 才是原子的），
写入后 `fsync` 再 `os.replace`，最后清理临时文件。任何一步失败都明确抛 `StorageError`。

【读的容错分级】
- 文件不存在 → 返回 default（正常，新用户首次启动）
- 文件是合法 JSON → 返回数据
- 文件损坏 → **保留原文件**（改名 `.corrupt` 留证）、记日志、返回 default，不崩
- 权限/IO 错误 → 抛 `StorageError`（这种不该吞，否则会静默丢数据）
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from soulmate.core.exceptions import StorageError
from soulmate.core.logging import get_logger

log = get_logger("soulmate.storage.atomic")


def ensure_dir(path: str | Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: str | Path, data: Any) -> None:
    """把数据原子地写成 JSON（ensure_ascii=False，中文可读）。"""
    path = Path(path)
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise StorageError(f"写入失败 {path}", details={"path": str(path), "cause": str(exc)}) from exc


def read_json(path: str | Path, default: Any = None) -> Any:
    """读 JSON。缺失/损坏返回 default；IO 错误抛 StorageError。"""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as exc:
        raise StorageError(f"读取失败 {path}", details={"path": str(path), "cause": str(exc)}) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # 损坏文件：改名留证（不覆盖，方便人工抢救），返回 default
        corrupt = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
        try:
            os.replace(path, corrupt)
        except OSError:
            pass
        log.error("JSON 损坏，已跳过并留证: %s -> %s (%s)", path, corrupt, exc)
        return default


def read_json_cached(cache: Any, path: str | Path, default: Any = None) -> Any:
    """带 mtime 缓存的读：文件没变就不重复读盘。`cache` 是 MTimeCache 实例。"""
    return cache.get(str(path), lambda: read_json(path, default))


def delete_file(path: str | Path) -> bool:
    """删文件；不存在返回 False；失败不抛（调用方无需 try）。"""
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("删除失败 %s: %s", path, exc)
        return False


def list_json_stems(dirpath: str | Path) -> list[str]:
    """列目录下所有 .json 的「去扩展名」文件名；目录不存在返回空列表。"""
    p = Path(dirpath)
    if not p.is_dir():
        return []
    return [f.stem for f in p.iterdir() if f.is_file() and f.suffix == ".json"]


def delete_dir(dirpath: str | Path) -> None:
    """递归删目录（删伴侣时连带删会话），忽略错误。"""
    import shutil

    shutil.rmtree(dirpath, ignore_errors=True)
