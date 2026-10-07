"""模型服务（Provider）业务层：校验 + CRUD + 占用检查 + 连通性测试。

【核心职责】
- 把界面来的**原始 dict**（未清洗）变成合法的 `Provider` 领域模型 —— 校验就在这里做；
- 删除前检查有没有伴侣正占用它（防止删掉后伴侣「失明」）；
- `test_connection()` 给诊断页用：真的打一次最小的模型请求看通不通。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from soulmate.core.exceptions import ProviderError, ValidationError
from soulmate.core.logging import get_logger
from soulmate.core.models import ChatMessage, Companion, Provider
from soulmate.core.settings import Settings
from soulmate.core.validation import validate_base_url, validate_model_name, validate_text
from soulmate.llm.service import LLMService
from soulmate.storage.repositories import ProviderRepository

log = get_logger("soulmate.services.provider")


class ProviderService:
    def __init__(self, repo: ProviderRepository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings

    # ── 读 ──
    def list(self) -> list[Provider]:
        return self._repo.list()

    def get(self, provider_id: str) -> Provider | None:
        return self._repo.get(provider_id)

    # ── 写（含校验）──
    def upsert(self, data: dict) -> Provider:
        """校验并保存。`data` 来自界面表单，可能是空串/超长/非法 URL。"""
        pid = str(data.get("id") or "").strip()
        base_url = validate_base_url(
            data.get("base_url"),
            allow_private=self._settings.allow_private_base_url,
        )
        model = validate_model_name(data.get("model"))
        preset = str(data.get("preset") or "custom").strip()
        alias = validate_text(data.get("alias") or "", field="别名", max_chars=60, allow_empty=True)
        api_key = str(data.get("api_key") or "").strip()

        provider = Provider(
            id=pid or str(uuid.uuid4()),
            preset=preset,
            alias=alias or (f"{model}" if model else ""),
            base_url=base_url,
            api_key=api_key,
            model=model,
            created_at=(data.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self._repo.upsert(provider)
        return provider

    def delete(self, provider_id: str) -> None:
        self._repo.delete(provider_id)

    def in_use(self, provider_id: str, companions: list[Companion]) -> list[str]:
        """返回正在使用该模型服务的伴侣名字（删除前要拦）。"""
        return [c.name for c in companions if c.provider_id == provider_id]

    # ── 连通性测试（诊断页用，真的调一次模型）──
    def test_connection(self, provider: Provider, llm: LLMService, timeout: float = 10.0) -> tuple[bool, float, str]:
        """返回 (是否成功, 耗时秒, 结果/错误文案)。失败也**不抛异常**，方便诊断页一行展示。"""
        if not provider.api_key:
            return False, 0.0, "没有配置 API Key（可留空走环境变量）"
        t0 = time.perf_counter()
        try:
            result = llm.chat(
                provider,
                [ChatMessage(role="user", content="回复「OK」两个字母即可")],
                max_tokens=16,
                timeout=timeout,
                max_retries=0,
                temperature=0,
            )
            latency = time.perf_counter() - t0
            text = (result.text or "").strip()
            return True, round(latency, 2), f"连通 ✓ 模型回复：{text[:40]}"
        except ProviderError as exc:
            latency = time.perf_counter() - t0
            return False, round(latency, 2), exc.user_message()
        except Exception as exc:  # noqa: BLE001 - 测试路径兜底
            latency = time.perf_counter() - t0
            log.warning("连通性测试异常: %s", exc)
            return False, round(latency, 2), "未知错误，请在日志里查看详情"