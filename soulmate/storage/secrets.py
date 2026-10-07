"""密钥加密的薄封装：按 app_secret 缓存 SecretBox，避免重复跑 PBKDF2。

PBKDF2 派生一次约几十毫秒，如果每个仓储实例都各自派生一把，会白浪费。
这里用 lru_cache 让「同一个 app_secret」全进程共享一个 Fernet 实例。
"""

from __future__ import annotations

from functools import lru_cache

from soulmate.core.security import SecretBox


@lru_cache(maxsize=16)
def get_cipher(app_secret: str) -> SecretBox:
    return SecretBox(app_secret)
