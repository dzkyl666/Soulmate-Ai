"""core 层：零业务依赖的基础设施。

本层不 import 任何业务模块，只依赖标准库 + pydantic/cryptography/bcrypt。
它是全项目的「地基」，任何一层都允许（且只允许）依赖它。
"""

from __future__ import annotations

__all__: list[str] = []
