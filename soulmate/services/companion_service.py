"""伴侣与会话业务层：项目里业务最重的模块。

管四件事：
1. 伴侣 CRUD（校验 + 人设占位符替换）
2. 会话 CRUD（标题三来源：手动 / AI 起名 / 自动取首句）
3. 系统提示词拼装（人设 + 使用者信息 + 长期记忆，**每次请求现拼**）
4. AI 起名 / 自动标题
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from soulmate.core.exceptions import ValidationError
from soulmate.core.logging import get_logger
from soulmate.core.models import ChatMessage, Companion, Profile, Provider, SessionDoc, SessionMeta
from soulmate.core.presets import DEFAULT_SYSTEM_PROMPT
from soulmate.core.settings import Settings
from soulmate.core.validation import validate_avatar, validate_text
from soulmate.llm.service import LLMService
from soulmate.storage.atomic import delete_dir
from soulmate.storage.repositories import CompanionRepository, MemoryRepository, SessionRepository

log = get_logger("soulmate.services.companion")


class CompanionService:
    def __init__(
        self,
        companions_repo: CompanionRepository,
        sessions_repo: SessionRepository,
        memory_repo: MemoryRepository,
        llm: LLMService,
        settings: Settings,
    ) -> None:
        self._companions = companions_repo
        self._sessions = sessions_repo
        self._memory = memory_repo
        self._llm = llm
        self._settings = settings

    # ══════════════════════════════════════════════════════════
    # 伴侣
    # ══════════════════════════════════════════════════════════
    def list(self) -> list[Companion]:
        return self._companions.list()

    def get(self, companion_id: str) -> Companion | None:
        return self._companions.get(companion_id)

    def upsert(self, data: dict) -> Companion:
        """校验 + 保存。名字必填；人设占位符按「先填后替换」处理。"""
        name = validate_text(data.get("name"), field="名称", max_chars=40, allow_empty=False)
        purpose = validate_text(data.get("purpose"), field="用途", max_chars=200, allow_empty=True)
        avatar = validate_avatar(data.get("avatar"))
        system_prompt = data.get("system_prompt") or ""

        companion = Companion(
            id=str(data.get("id") or "").strip() or str(uuid.uuid4()),
            name=name,
            purpose=purpose,
            avatar=avatar,
            system_prompt=self._finish_prompt(system_prompt, name, purpose),
            provider_id=str(data.get("provider_id") or "").strip(),
            fallback_provider_id=str(data.get("fallback_provider_id") or "").strip(),
            created_at=(data.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self._companions.upsert(companion)
        return companion

    @staticmethod
    def _finish_prompt(text: str, name: str, purpose: str) -> str:
        """替换人设里的 {name} / {purpose}；留空用默认模板。"""
        text = (text or "").strip()
        values = {"name": name, "purpose": purpose or "温柔体贴"}
        if not text:
            return DEFAULT_SYSTEM_PROMPT.format(**values)
        if "{name}" in text or "{purpose}" in text:
            try:
                return text.format(**values)
            except Exception:
                return text
        return text

    def delete(self, companion_id: str) -> None:
        """删伴侣：同时清掉它的会话目录和长期记忆文件（不留孤儿数据）。"""
        self._companions.delete(companion_id)
        delete_dir(self._sessions.sessions_dir(companion_id))
        self._memory.clear(companion_id)

    # ══════════════════════════════════════════════════════════
    # 会话
    # ══════════════════════════════════════════════════════════
    @staticmethod
    def new_session_id() -> str:
        """会话 ID = 时间戳 + 8 位随机串。

        纯秒级时间戳有个洞：同一秒连点两次「新建会话」会撞 ID。
        补随机串后碰撞概率可忽略；前缀仍是时间戳，倒序排列天然按时间新在前。
        """
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return f"{ts}_{uuid.uuid4().hex[:8]}"

    def list_metas(self, companion_id: str) -> list[SessionMeta]:
        return self._sessions.list_meta(companion_id)

    def load_messages(self, companion_id: str, session_id: str) -> list[ChatMessage]:
        doc = self._sessions.load(companion_id, session_id)
        return list(doc.messages) if doc else []

    def session_title(self, companion_id: str, session_id: str) -> str:
        doc = self._sessions.load(companion_id, session_id)
        return doc.title or "新会话" if doc else "新会话"

    def save_messages(
        self,
        companion_id: str,
        session_id: str,
        messages: list[ChatMessage],
        *,
        title: str | None = None,
        title_source: str | None = None,
    ) -> None:
        """保存会话。空会话不落盘（删掉旧文件）。

        标题三来源（优先级从高到低）：
            调用方显式传 title → 用它（source: ai/manual）
            文件里已有 title    → 沿用
            都没有              → 自动取首条用户消息前 20 字（auto）
        """
        if not companion_id or not session_id:
            return
        old = self._sessions.load(companion_id, session_id)
        if not messages:
            # 一条消息都没有的会话不落盘，避免留垃圾文件
            self._sessions.delete(companion_id, session_id)
            return

        if title is not None:
            final_title, final_src = title, title_source or "manual"
        else:
            final_title = (old.title if old else "") or self.auto_title(messages)
            final_src = (old.title_source if old else "auto")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        doc = SessionDoc(
            session_id=session_id,
            title=final_title,
            title_source=final_src,
            created_at=old.created_at if old else now,
            updated_at=now,
            messages=messages,
        )
        self._sessions.save(companion_id, doc)

    def delete_session(self, companion_id: str, session_id: str) -> None:
        self._sessions.delete(companion_id, session_id)

    def rename_session(self, companion_id: str, session_id: str, title: str) -> None:
        title = validate_text(title, field="会话标题", max_chars=60, allow_empty=False)
        old = self._sessions.load(companion_id, session_id)
        if old is None:
            return
        self._sessions.save(
            companion_id,
            old.model_copy(update={"title": title, "title_source": "manual"}),
        )

    @staticmethod
    def auto_title(messages: list[ChatMessage]) -> str:
        """取第一条非空用户消息前 20 字当标题。"""
        for m in messages:
            if m.role == "user":
                text = m.content.replace("\n", " ").strip()
                if text:
                    return text[:20] + ("…" if len(text) > 20 else "")
        return "新会话"

    def generate_ai_title(self, provider: Provider, fallback: Provider | None, messages: list[ChatMessage]) -> str:
        """让伴侣绑定的模型给会话起精炼标题（非流式，一次调用）。"""
        convo = "\n".join(
            f"{'用户' if m.role == 'user' else '伴侣'}：{m.content}" for m in messages[:6]
        )
        result = self._llm.chat(
            provider,
            [ChatMessage(role="user", content=convo)],
            system_prompt=(
                "你是标题生成器。用不超过 12 个字概括这段对话的主题，"
                "只输出标题本身，不要引号、不要标点、不要解释。"
            ),
            fallback_cfg=fallback,
            temperature=0.2,
            max_tokens=32,
        )
        return result.text.strip("《》\"'。.、 ")

    # ══════════════════════════════════════════════════════════
    # 系统提示词（每次请求现拼）
    # ══════════════════════════════════════════════════════════
    def build_system_prompt(self, companion: Companion, profile: Profile, memory_block: str = "") -> str:
        """拼出当次请求的 system prompt。

        为什么必须**现拼**而不是存一份：
        - 使用者昵称是全局资料，改一次要所有伴侣都同步 → 现拼永远同步；
        - 长期记忆每轮都可能新增 → 现读现拼，刚记住的下一轮立刻生效。
        """
        prompt = (companion.system_prompt or "").strip()
        if "{name}" in prompt or "{purpose}" in prompt:
            try:
                prompt = prompt.format(name=companion.name, purpose=companion.purpose or "温柔体贴")
            except Exception:
                pass

        nickname = (profile.nickname or "").strip()
        if nickname:
            prompt += (
                f"\n\n【正在和你聊天的人】对方的名字叫「{nickname}」。"
                f"在合适的时候可以自然地称呼 TA 的名字，但不必每句话都叫。"
            )

        if memory_block:
            prompt += memory_block
        return prompt