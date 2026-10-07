"""Provider 仓储：负责模型服务的 CRUD，且是**唯一**处理 API Key 落盘加密的地方。

【职责边界】
- 内存里的 `Provider.api_key` 是明文（要给 llm 层用）
- 磁盘上的记录里只有 `api_key_enc`（Fernet 密文），**绝不出现明文 key**
- 兼容旧数据：老版本存的是明文 `api_key`，读到后照常用，下次保存时自动转成密文

【关于「环境变量兜底」】
v1 有一条链路：界面上 Key 留空 → 读该预设对应的同名环境变量。
v2 重构时丢了这条（见 docs/MIGRATION.md 的「v1 → v2 行为变化清单」），现已恢复。

实现上**本层不读环境变量**：容器把 `Settings.provider_env_api_key` 作为 callable
注进来（`api_key_fallback`）。这样既守住了「配置只有一个来源」的分层原则，
又保证了**不可能漏调用点** —— 凡是走出仓储的 Provider 都会经过这里。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from soulmate.core.logging import get_logger
from soulmate.core.models import Provider
from soulmate.core.security import SecretBox
from soulmate.storage.atomic import atomic_write_json, read_json

log = get_logger("soulmate.storage.repos.providers")

# 在磁盘记录里，明文 key 的合法字段名（写入时禁止出现）
_PLAINTEXT_KEYS = ("api_key", "api_key_enc")


class ProviderRepository:
    """模型服务池。按用户目录隔离，独立于基类（因需要加密钩子）。"""

    FILE = "providers.json"

    def __init__(
        self,
        root: Path,
        cipher: SecretBox,
        api_key_fallback: Callable[[str], str] | None = None,
    ) -> None:
        """`api_key_fallback(preset) -> key`：Key 为空时的**最后**兜底（可注入）。

        传 `None` 表示不启用兜底（生产默认，或测试想验证"没有兜底"的行为）。
        """
        self._path = Path(root) / self.FILE
        self._cipher = cipher
        self._api_key_fallback = api_key_fallback

    # ── 记录 ↔ 模型 ──
    def _to_model(self, record: dict) -> Provider | None:
        if not isinstance(record, dict):
            log.warning("忽略非法 provider 记录: %r", record)
            return None

        api_key = ""
        enc = record.get("api_key_enc") or ""
        if enc:
            try:
                api_key = self._cipher.decrypt(enc)
            except ValueError:
                # 密钥失效（如 app_secret 变更）：不崩，置空让用户重填
                log.warning("provider %s 的密钥无法解密，已置空（可能 app_secret 变更）", record.get("id"))
        elif record.get("api_key"):
            # 老版本明文：直接读，下次 upsert 时自动加密
            api_key = str(record["api_key"])

        # 第三优先级：环境变量兜底（v1 行为）。
        # 优先级必须是「密文 > 旧明文 > 环境变量」—— 环境变量**只兜底**，
        # 绝不能覆盖用户在界面上显式填的 Key（那会让人以为自己的 Key 没生效）。
        # 取不到就静默保持空串：没设环境变量是正常情况，不是错误。
        if not api_key and self._api_key_fallback is not None:
            api_key = self._api_key_fallback(str(record.get("preset") or ""))

        clean = {k: v for k, v in record.items() if k not in _PLAINTEXT_KEYS}
        try:
            return Provider(api_key=api_key, **clean)
        except Exception:
            log.exception("provider 记录解析失败: %s", clean.get("id"))
            return None

    def _to_record(self, provider: Provider) -> dict:
        rec = provider.model_dump(exclude={"api_key"})
        if provider.api_key:
            rec["api_key_enc"] = self._cipher.encrypt(provider.api_key)
        return rec

    # ── CRUD ──
    def list(self) -> list[Provider]:
        return [p for p in (self._to_model(r) for r in (read_json(self._path, []) or [])) if p is not None]

    def get(self, provider_id: str) -> Provider | None:
        return next((p for p in self.list() if p.id == provider_id), None)

    def upsert(self, provider: Provider) -> Provider:
        items = [self._to_record(p) for p in self.list()]
        idx = next((i for i, p in enumerate(items) if p.get("id") == provider.id), None)
        rec = self._to_record(provider)
        if idx is None:
            items.append(rec)
        else:
            items[idx] = rec
        atomic_write_json(self._path, items)
        return provider

    def delete(self, provider_id: str) -> None:
        items = [self._to_record(p) for p in self.list() if p.id != provider_id]
        atomic_write_json(self._path, items)
