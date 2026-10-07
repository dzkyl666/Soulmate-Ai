"""Provider 工厂：按配置构造具体的 LLMProvider 实例。

现在只有 OpenAI 兼容一种实现；将来要接 Anthropic 原生 / Gemini 原生，
在这里加分支即可，上层（services / ui）一行不用改。
"""

from __future__ import annotations

from soulmate.core.models import Provider
from soulmate.llm.base import LLMProvider
from soulmate.llm.openai_provider import OpenAIProvider


class ProviderRegistry:
    def build(self, cfg: Provider) -> LLMProvider:
        """按一个 Provider 配置构造可调用的 provider。"""
        if cfg.base_url:
            return OpenAIProvider(api_key=cfg.api_key, base_url=cfg.base_url, label=cfg.label)
        # 没有 base_url 时用 SDK 默认（OpenAI 官方地址），仍走 OpenAI 兼容路径
        return OpenAIProvider(api_key=cfg.api_key, base_url=None, label=cfg.label)


_default_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    return _default_registry