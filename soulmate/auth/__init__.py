"""服务容器 + 认证，均不对 UI 层暴露持久化细节。

export:
- ServiceContainer（见 container.py）
- UserStore / issue_session / OIDC 适配（见 auth/）
"""

from __future__ import annotations

__all__: list[str] = []
