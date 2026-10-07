"""llm 层：所有模型调用都从这里出发。

全项目唯一 `import openai` 的地方是 `openai_provider.py`。
上层（services / ui）只认识 `LLMProvider` 接口和 `StreamEvent` 事件流，
不认识 SDK —— 换厂商、加重试、接 Function Calling 都只改这一层。
"""

from __future__ import annotations

from soulmate.llm.service import LLMService

__all__ = ["LLMService"]