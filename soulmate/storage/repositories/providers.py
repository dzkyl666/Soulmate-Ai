"""Provider 仓储：负责模型服务的 CRUD，且是**唯一**处理 API Key 落盘加密的地方。

【职责边界】
- 内存里的 `Provider.api_key` 是明文（要给 llm 层用）
- 磁盘上的记录里只有 `api_key_enc`（Fernet 密文），**绝不出现明文 key**
- 兼容旧数据：老版本存的是明文 `api_key`，读到后照常用，下次保存时自动转成密文
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from soulmate.core.logging import get_logger
from soulmate.core.models import Provider
from soulmate.core.security import SecretBox
from soulmate.storage.atomic import atomic_write_json, read_json
from soulmate.storage.repositories.base import ListRepository

log = get_logger("soulmate.storage.repos.providers")

# 在磁盘记录里，明文 key 的合法字段名（写入时禁止出现）
_PLAINTEXT_KEYS = ("api_key", "api_key_enc")


class ProviderRepository:
    """模型服务池。按用户目录隔离，独立于基类（因需要加密钩子）。"""

    FILE = "providers.json"

    def __init__(self, root: Path, cipher: SecretBox) -> None:
        self._path = Path(root) / self.FILE
        self._cipher = cipher

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