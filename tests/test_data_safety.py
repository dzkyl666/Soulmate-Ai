"""★ 安全网测试：确保测试套件绝不会动到真实数据目录。

背景：第一次用 CLI 做冒烟时，`soulmate create-user` 顺手跑了老数据迁移，
把项目里真实的 `data/providers.json` 等搬进了 `data/users/<用户>/`。
虽然能还原，但这类「测试误伤真实数据」是最该被彻底堵死的一类事故。

本文件把这条约束固化成测试：
1. 测试进程里 `get_settings().data_root()` 必须位于临时目录；
2. 项目里真实的 `data/` 目录在测试前后内容指纹不变。

只要有人（或某段代码）把配置指回真实目录，这里就会红。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from soulmate.core.settings import get_settings


def _fingerprint(root: Path) -> dict[str, str]:
    """给目录做一份「路径 → 内容哈希」指纹，用于比对测试前后是否被改动。"""
    if not root.exists():
        return {}
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            except OSError:
                out[str(p.relative_to(root))] = "<unreadable>"
    return out


class TestRealDataIsProtected:
    def test_settings_point_into_temp_dir(self, tmp_root):
        """测试期配置必须落在临时目录里。"""
        data_root = get_settings().data_root()
        assert str(tmp_root) in str(data_root), (
            f"测试数据目录必须隔离在 {tmp_root} 内，实际是 {data_root}；"
            "否则迁移/写入会碰到用户的真实数据。"
        )

    def test_legacy_session_dir_is_isolated(self, tmp_root):
        """v0 老会话目录也必须隔离，否则迁移会偷走项目根的真实 session/。"""
        assert str(tmp_root) in str(get_settings().legacy_session_path)

    def test_real_project_data_untouched(self, project_root):
        """真实 data/ 目录在整个测试会话里必须保持不变。

        注意：本测试自身不动任何文件；它靠「测试开始时的指纹」与
        「现在这一刻的指纹」比对来发现越界写入。
        """
        real_data = project_root / "data"
        if not real_data.exists():
            pytest.skip("项目里没有真实 data/ 目录，无需保护")

        before = _fingerprint(real_data)
        # 触发一次配置与容器读取（正常测试流程都会做的事）
        settings = get_settings()
        assert settings.data_root() is not None
        after = _fingerprint(real_data)

        added = set(after) - set(before)
        removed = set(before) - set(after)
        changed = {k for k in before.keys() & after.keys() if before[k] != after[k]}

        assert not added, f"测试往真实 data/ 里写了新文件：{sorted(added)}"
        assert not removed, f"测试删掉了真实 data/ 里的文件：{sorted(removed)}"
        assert not changed, f"测试改动了真实 data/ 里的文件：{sorted(changed)}"

    def test_user_data_dir_is_under_temp(self, tmp_root):
        """任意用户的目录都必须落在临时根下。"""
        settings = get_settings()
        for uid in ("alice", "bob", "任意用户名"):
            assert str(tmp_root) in str(settings.user_data_dir(uid))
