"""★ 环境变量兜底：v1 有 → v2 重构时丢失 → 现已恢复。

【这类回归为什么必须双向测】
它是典型的「承诺还在、实现没了」：
- `README.md`、`ui/dialogs.py` 的输入框标签、提示文案、`ProviderService.test_connection`
  的返回语 —— **四处仍在承诺**「Key 留空会读同名环境变量」
- `core/presets.py` 里 8 个预设的 `env_key` 字段也都还在
- 只有实现没了（`git log -S getenv` 可自证 v2 从未实现）

后果不是"少个便利功能"：真实用户的 `providers.json` 里 `api_key` 是 `""`（v1 就这么存），
Key 一直只放在系统环境变量里 → **聊天功能完全不可用**，而报错文案还把人引向"充值"。

所以本文件的断言覆盖四个面：
1. 取到了吗（正向）
2. 优先级对不对（显式 Key 绝不能被环境变量盖掉）
3. 取不到会不会抛（兜底必须是静默的）
4. 策略默认值对不对（生产默认关 —— 这是安全决策，见 docs/SECURITY.md 取舍表）
"""

from __future__ import annotations

import json

import pytest

from soulmate.core.settings import Settings
from soulmate.services.container import ServiceContainer
from soulmate.storage.secrets import get_cipher

ENV_NAME = "SILICONFLOW_API_KEY"
PRESET = "siliconflow"
BASE_URL = "https://api.siliconflow.cn/v1"
FORM = {"preset": PRESET, "base_url": BASE_URL, "model": "deepseek-ai/DeepSeek-V4-Flash"}


def _settings(tmp_path, *, env: str = "test", fallback: bool | None = None) -> Settings:
    return Settings(
        env=env,
        app_secret="t" * 48,
        data_dir=tmp_path / "data",
        legacy_session_dir=tmp_path / "legacy-session",
        provider_env_fallback=fallback,
        max_retries=0,
    )


def _write_raw(settings: Settings, uid: str, record: dict) -> None:
    """直接写一条原始记录：用来精确构造「密文 / 旧明文 / 空」三种存储状态。"""
    path = settings.user_data_dir(uid) / "providers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([record], ensure_ascii=False), encoding="utf-8")


def _base_record(**over: object) -> dict:
    rec = {
        "id": "p1",
        "preset": PRESET,
        "alias": "测试",
        "base_url": BASE_URL,
        "model": "m",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    rec.update(over)
    return rec


# ══════════════════════════════════════════════════════════
# 1. 兜底真的生效（正向）
# ══════════════════════════════════════════════════════════
class TestFallbackTakesEffect:
    def test_env_var_used_when_stored_key_is_empty(self, tmp_path, monkeypatch):
        """① 环境变量有值 + 存储的 Key 为空 ⇒ 能取到。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": ""})

        assert svc.providers.list_providers()[0].api_key == "sk-from-env"

    def test_fallback_does_not_leak_key_to_disk(self, tmp_path, monkeypatch):
        """兜底只影响内存，**磁盘上仍不许出现明文**（安全不允许倒退）。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": ""})

        raw = (settings.user_data_dir("alice") / "providers.json").read_text(encoding="utf-8")
        assert "sk-from-env" not in raw
        # 空 Key 时磁盘上**两个 key 字段都不出现**（不是存一个空串）——
        # 也就是说「靠环境变量」的 provider 落盘后完全不含任何 key 物料。
        assert "api_key" not in raw

    def test_get_single_provider_also_benefits(self, tmp_path, monkeypatch):
        """单条读取（伴侣绑定的那条路）也要兜底，不能只在列表里生效。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        pid = svc.providers.upsert({**FORM, "api_key": ""}).id

        assert svc.provider_repo.get(pid).api_key == "sk-from-env"


# ══════════════════════════════════════════════════════════
# 2. 优先级：环境变量**只兜底**，绝不覆盖显式 Key
# ══════════════════════════════════════════════════════════
class TestPriorityExplicitWins:
    """要求：`api_key_enc` 解密结果 > 旧明文 `api_key` > 环境变量 > 空。

    优先级反了会很坑：用户在界面填了 Key 却"不生效"，会以为程序坏了。
    """

    def test_explicit_key_saved_by_ui_wins(self, tmp_path, monkeypatch):
        """② 显式 Key 也填了 ⇒ 用显式的（走界面 upsert，落成密文）。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": "sk-explicit"})

        assert svc.providers.list_providers()[0].api_key == "sk-explicit"

    def test_legacy_plaintext_wins_over_env(self, tmp_path, monkeypatch):
        """旧明文 Key 也优先于环境变量。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, fallback=True)
        _write_raw(settings, "alice", _base_record(api_key="sk-legacy-plain"))

        assert ServiceContainer(settings, "alice").providers.list_providers()[0].api_key == "sk-legacy-plain"

    def test_encrypted_wins_over_everything(self, tmp_path, monkeypatch):
        """密文优先级最高：即使记录里同时存在旧明文与环境变量。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, fallback=True)
        enc = get_cipher(settings.app_secret).encrypt("sk-encrypted")
        _write_raw(settings, "alice", _base_record(api_key_enc=enc, api_key="sk-legacy-plain"))

        assert ServiceContainer(settings, "alice").providers.list_providers()[0].api_key == "sk-encrypted"


# ══════════════════════════════════════════════════════════
# 3. 取不到时的行为：静默返回空，绝不抛
# ══════════════════════════════════════════════════════════
class TestNoFallbackIsSilent:
    def test_no_env_var_returns_empty_without_raising(self, tmp_path, monkeypatch):
        """③ 都没有 ⇒ 返回空且**不抛异常**（没设环境变量是正常情况，不是错误）。"""
        monkeypatch.delenv(ENV_NAME, raising=False)
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": ""})

        providers = svc.providers.list_providers()  # ← 这里不抛才算过
        assert providers[0].api_key == ""

    def test_custom_preset_has_no_env_key(self, tmp_path, monkeypatch):
        """`custom` 预设的 `env_key` 是空串 ⇒ 不兜底，也不报错。"""
        monkeypatch.setenv("CUSTOM_API_KEY", "should-not-be-used")
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({"preset": "custom", "base_url": "https://x.com/v1", "model": "m", "api_key": ""})

        assert svc.providers.list_providers()[0].api_key == ""

    def test_blank_env_var_counts_as_empty(self, tmp_path, monkeypatch):
        """环境变量只有空白字符 ⇒ 视同没设（否则会拿一个空 Key 去发请求）。"""
        monkeypatch.setenv(ENV_NAME, "   ")
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": ""})

        assert svc.providers.list_providers()[0].api_key == ""

    def test_unknown_preset_is_safe(self, tmp_path, monkeypatch):
        """预设名不在预设表里 ⇒ 查不到 env_key，静默返回空。"""
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({"preset": "no-such-preset", "base_url": "https://x.com/v1", "model": "m", "api_key": ""})

        assert svc.providers.list_providers()[0].api_key == ""


# ══════════════════════════════════════════════════════════
# 4. 策略默认值（安全决策，必须显式钉住）
# ══════════════════════════════════════════════════════════
class TestPolicyDefault:
    """★ 生产默认关 —— 这是本项目最需要留意的安全取舍之一。

    环境变量是**服务器级**凭证：兜底一旦在生产生效，任何能登录的用户
    只要把 Key 留空，就能白用**服务器所有者**的额度。所以生产默认关。
    而非生产默认开，是因为关掉等于让所有 v1 老用户（Key 只存在环境变量里）
    的聊天直接不可用 —— 这正是我们要修的那个回归。
    """

    @pytest.mark.parametrize(
        ("env", "explicit", "expected"),
        [
            ("development", None, True),    # 单机/开发：默认开（否则老用户直接不可用）
            ("test", None, True),
            ("production", None, False),    # ★ 生产：默认关（防白用服务器额度）
            ("production", True, True),     # 显式开：单人自部署 + production 也能用
            ("development", False, False),  # 显式关
        ],
    )
    def test_resolved_policy(self, tmp_path, env, explicit, expected):
        assert _settings(tmp_path, env=env, fallback=explicit).provider_env_fallback_enabled is expected

    def test_production_default_end_to_end_no_fallback(self, tmp_path, monkeypatch):
        """生产 + 未显式设置 ⇒ 环境变量里有值也**不**兜底。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, env="production")
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": ""})

        assert svc.providers.list_providers()[0].api_key == ""

    def test_explicit_off_ignores_env_var(self, tmp_path, monkeypatch):
        """显式关掉兜底 ⇒ 环境变量里的 Key 绝不会被用（安全侧反向断言）。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, fallback=False)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": ""})

        assert svc.providers.list_providers()[0].api_key == ""

    def test_production_explicit_on_does_fall_back(self, tmp_path, monkeypatch):
        """显式打开后，生产环境也能兜底（策略可覆盖，不是把人锁死）。"""
        monkeypatch.setenv(ENV_NAME, "sk-from-env")
        settings = _settings(tmp_path, env="production", fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": ""})

        assert svc.providers.list_providers()[0].api_key == "sk-from-env"


# ══════════════════════════════════════════════════════════
# 5. 兜底只认「预设的 env_key」，不瞎猜名字
# ══════════════════════════════════════════════════════════
class TestEnvNameIsFromPreset:
    def test_only_preset_env_key_is_read(self, tmp_path, monkeypatch):
        """只有预设表里声明的那个变量名会被读，别的同名无关变量不参与。"""
        monkeypatch.setenv("SILICONFLOW_API_KEY_EXTRA", "sk-wrong")
        monkeypatch.setenv(ENV_NAME, "sk-right")
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert({**FORM, "api_key": ""})

        assert svc.providers.list_providers()[0].api_key == "sk-right"

    @pytest.mark.parametrize("preset", ["openai", "deepseek", "siliconflow", "dashscope"])
    def test_all_presets_with_env_key_are_wired(self, tmp_path, monkeypatch, preset):
        """遍历几个真实预设，确认 env_key 链路对每个预设都通（不是只对某一个）。"""
        from soulmate.core.presets import PROVIDER_PRESETS

        env_name = str(PROVIDER_PRESETS[preset]["env_key"])
        monkeypatch.setenv(env_name, f"sk-{preset}")
        settings = _settings(tmp_path, fallback=True)
        svc = ServiceContainer(settings, "alice")
        svc.providers.upsert(
            {"preset": preset, "base_url": "https://example.com/v1", "model": "m", "api_key": ""}
        )

        assert svc.providers.list_providers()[0].api_key == f"sk-{preset}"
