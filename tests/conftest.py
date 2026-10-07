"""pytest 全局夹具与环境隔离。

【最重要的两条】
1. **绝不碰真实数据**：把 `SOULMATE_DATA_DIR` 和 `SOULMATE_LEGACY_SESSION_DIR`
   都指到临时目录，确保迁移逻辑不会误动项目里真实的 `data/` 与 `session/`。
2. **每个测试一个独立数据目录**：`settings` 夹具用 `tmp_path`，
   测试之间零共享状态，能并行也不互相污染。

这些环境变量必须在**导入 soulmate 之前**设好，否则 `get_settings()` 会缓存住默认值。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ── 进程级测试环境（导入 soulmate 之前设置）──
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="soulmate-pytest-"))
(_TMP_ROOT / "legacy-session").mkdir(parents=True, exist_ok=True)

os.environ["SOULMATE_ENV"] = "test"
os.environ["SOULMATE_DEBUG"] = "false"
os.environ["SOULMATE_APP_SECRET"] = "t" * 48
os.environ["SOULMATE_DATA_DIR"] = str(_TMP_ROOT / "data")
os.environ["SOULMATE_LEGACY_SESSION_DIR"] = str(_TMP_ROOT / "legacy-session")
os.environ["SOULMATE_AUTH_MODE"] = "local"
os.environ["SOULMATE_ALLOW_SIGNUP"] = "true"
os.environ["SOULMATE_BACKUP_KEEP"] = "2"
os.environ["SOULMATE_MAX_MESSAGE_CHARS"] = "8000"
os.environ["SOULMATE_REQUEST_TIMEOUT_SECONDS"] = "5"
os.environ["SOULMATE_EXTRACT_TIMEOUT_SECONDS"] = "3"
os.environ["SOULMATE_MAX_RETRIES"] = "0"

# 让 `import soulmate` 生效（从项目根运行 pytest 时本就可达，这里显式兜底）
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest  # noqa: E402

from soulmate.core.settings import Settings, get_settings  # noqa: E402
from soulmate.services.container import ServiceContainer  # noqa: E402

TEST_SECRET = "t" * 48


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """会话结束清理临时目录与配置缓存。"""
    get_settings.cache_clear()
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture
def project_root() -> Path:
    return _PROJECT_ROOT


@pytest.fixture
def tmp_root() -> Path:
    """进程级临时根（AppTest 会用同一份，因为 settings 是全局缓存的）。"""
    return _TMP_ROOT


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """每个测试独立数据目录的配置对象。"""
    return Settings(
        env="test",
        app_secret=TEST_SECRET,
        data_dir=tmp_path / "data",
        legacy_session_dir=tmp_path / "legacy-session",
        backup_keep=2,
        max_retries=0,
        request_timeout_seconds=5,
        extract_timeout_seconds=3,
    )


@pytest.fixture
def container(settings: Settings) -> ServiceContainer:
    """一个属于 "tester" 用户的服务容器。"""
    return ServiceContainer(settings, "tester")


@pytest.fixture
def fake_registry():
    """可编程的假 Provider 注册表，用来测降级链/流式，不联网。"""
    from tests.fakes import FakeRegistry

    return FakeRegistry()