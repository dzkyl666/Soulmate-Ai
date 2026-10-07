"""storage 层：把「数据怎么安全落盘」集中在这里。

上层 services 只知道「list/get/upsert/delete」，不关心文件、JSON、加密细节。
"""

from __future__ import annotations

__all__: list[str] = []
