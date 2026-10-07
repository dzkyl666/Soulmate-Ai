"""长期记忆业务层：抽取 → 去重入库 → 拼 prompt 块。

【抽取的三个设计决定】（想直接背的面试点）
① 只喂最近几条对话，不喂全部历史：记忆该记的事通常在刚才几轮，更早的早抽过了；
② 抽取失败一律静默：记忆是锦上添花，绝不能把聊天主流程带崩；
③ 用 max_retries=0 + 短超时：抽取失败不值得重试，白等是最坏结果。
"""

from __future__ import annotations

import json

from soulmate.core.exceptions import RateLimitError
from soulmate.core.logging import get_logger
from soulmate.core.models import ChatMessage, MemoryFact, Provider
from soulmate.core.presets import EXTRACT_PROMPT
from soulmate.core.ratelimit import get_limiter
from soulmate.core.settings import Settings
from soulmate.llm.service import LLMService
from soulmate.storage.repositories import MemoryRepository

log = get_logger("soulmate.services.memory")


class MemoryService:
    def __init__(self, repo: MemoryRepository, llm: LLMService, settings: Settings, user_id: str = "") -> None:
        self._repo = repo
        self.llm = llm
        self._settings = settings
        # 限流 key 用（与 LLMService 同样由容器注入）
        self._user_id = user_id

    # ── 读 ──
    @property
    def user_id(self) -> str:
        """本服务归属的用户（限流 key 的一部分）。"""
        return self._user_id

    def list_facts(self, companion_id: str) -> list[MemoryFact]:
        return self._repo.list_facts(companion_id)

    def forget_fact(self, companion_id: str, fact_id: str) -> None:
        self._repo.remove_fact(companion_id, fact_id)

    def forget_all(self, companion_id: str) -> None:
        self._repo.clear(companion_id)

    def to_prompt_block(self, facts: list[MemoryFact], limit: int | None = None) -> str:
        """拼成能贴进 system prompt 的一段；没有记忆返回空串。

        取**最近** limit 条：越近的记忆通常越相关；记忆会随时间增长，全塞必烧 token。
        """
        limit = self._settings.memory_inject_limit if limit is None else limit
        facts = facts[-limit:]
        if not facts:
            return ""
        lines = "\n".join(f"- {f.text}" for f in facts)
        return (
            "\n\n【你记得关于 TA 的事】\n"
            f"{lines}\n"
            "像老朋友一样自然地把这些用上，但不要生硬地逐条复述。"
        )

    # ── 写（抽取）──
    def remember_from_exchange(
        self,
        companion_id: str,
        messages: list[ChatMessage],
        provider: Provider,
        fallback: Provider | None = None,
        *,
        keep_last: int = 6,
    ) -> int:
        """从最近几轮对话抽「值得长期记住的事」入库，返回新增条数。

        【失败语义刻意分两种，别混】
        - **限流**：抛 `RateLimitError`。这是「你被限速了」的运维信号，不该静默掉；
        - **其它失败**（模型报错 / 网络抖 / 抽不出东西）：静默返回 0 ——
          记忆是锦上添花，绝不能把聊天主流程带崩。

        注意：这里记的是 `extract:` 那一档，而内部 `llm.chat()` 还会再记一次
        `chat:` 那一档 —— 即**一次抽取消耗两档配额**，这个相互作用已写进 SECURITY.md。
        """
        if not provider or not messages:
            return 0

        get_limiter().hit(
            f"extract:{self._user_id or 'anonymous'}",
            limit=self._settings.rate_limit_extract_per_minute,
            per_seconds=60,
        )

        recent = messages[-keep_last:]
        convo = "\n".join(
            f"{'用户' if m.role == 'user' else '伴侣'}：{m.content}" for m in recent
        )
        try:
            result = self.llm.chat(
                provider,
                [ChatMessage(role="user", content=convo)],
                system_prompt=EXTRACT_PROMPT,
                fallback_cfg=fallback,
                temperature=0,
                max_tokens=256,
                timeout=self._settings.extract_timeout_seconds,
                max_retries=0,  # 实测：默认重试 2 次在失败路径上要白等 10 秒
            )
        except RateLimitError:
            # ★ 限流**不静默**：抽取内部走的是 llm.chat()，会吃 chat 档配额；
            #   配额用尽属于「可观测的运维信号」，必须冒出去让调用方/界面知道。
            #   若在这里被下面的 except Exception 吞掉，就会出现
            #   「extract 档用尽会报错、chat 档用尽却静默」的不一致语义。
            raise
        except Exception:
            log.warning("记忆抽取失败，静默跳过", exc_info=True)
            return 0

        facts = self.parse_extraction(result.text)
        session_id = recent[-1].content[:24] if recent else ""  # 只作溯源标签
        if facts:
            return self._repo.add_facts(
                companion_id,
                facts,
                session_id=session_id,
                max_facts=self._settings.memory_max_facts,
            )
        return 0

    # ── 解析（模型输出不可信，必须兜底）──
    @staticmethod
    def parse_extraction(raw: str | None) -> list[str]:
        """把模型返回的文本解析成事实列表，解析失败返回 []（绝不抛异常）。

        实测要兜四种情况：包了 ```json 代码块 / 前后带解释文字 / 压根不是 JSON / 空串。
        """
        if not raw:
            return []
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        text = text[start : end + 1]
        try:
            data = json.loads(text)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [str(x).strip() for x in data if str(x).strip()]
