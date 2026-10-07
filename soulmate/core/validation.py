"""输入校验：把「不合法输入挡在业务逻辑之外」。

【为什么要集中做校验】
重构前，`base_url`、模型名、昵称、emoji 全都「来者不拒」，直接进字典落盘。
后果：① 空/非法 URL 到真正调用时才报错，用户看一脸懵；② 用户能填 `file://`、内网地址，
服务器替他去请求 → SSRF；③ 无长度上限，一条消息贴十万字能把 token 烧光。

本模块的每个函数都「要么返回清洗后的合法值，要么抛 `ValidationError`」。
界面层捕获 ValidationError 后把 `e.user_message()` 显示给用户。
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

from soulmate.core.exceptions import ValidationError
from soulmate.core.settings import get_settings

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
"""ID 合法字符集。刻意排除 / \\ 等路径分隔符，从根上挡住路径穿越。"""


def validate_id(value: object, *, field: str = "ID") -> str:
    """校验并返回合法 ID。用于文件名拼接，所以绝不允许路径分隔符。

    这是「路径穿越」的防线：session_id / companion_id 一旦允许 `../`，
    用户就能把文件写到目录外。
    """
    s = str(value or "").strip()
    if not s or not _ID_RE.match(s):
        raise ValidationError(f"{field} 不合法")
    return s


def validate_text(value: object, *, field: str = "内容", max_chars: int, allow_empty: bool = True) -> str:
    """校验自由文本的长度。返回 strip 后的值。"""
    s = str(value or "").strip()
    if not s and not allow_empty:
        raise ValidationError(f"{field} 不能为空")
    if len(s) > max_chars:
        raise ValidationError(f"{field} 太长（最多 {max_chars} 字）")
    return s


def validate_username(value: object) -> str:
    """用户名：小写化、限字符集与长度。"""
    s = str(value or "").strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9_.\-]{2,31}$", s):
        raise ValidationError("用户名需 3-32 位，只能含字母/数字/._-，且以字母或数字开头")
    return s


def validate_avatar(value: object) -> str:
    """头像：允许任意单字符 emoji（或短字符串）。不做过度限制，但要防超长。"""
    s = str(value or "").strip()
    if len(s) > 8:
        raise ValidationError("头像请用 1 个 emoji")
    return s


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_private        # RFC1918 + 唯一本地地址
        or ip.is_loopback    # 127.x
        or ip.is_link_local  # 169.254.x
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _hostname_resolves_to_private(host: str) -> bool:
    """判断主机名是否解析到私有/内网地址。解析失败时 fail-open（放行）并返回 False。

    为什么 fail-open：离线开发时 DNS 解析必然失败，若此时直接拦，用户连本地都配不了。
    这层防护的定位是「挡住明显的 SSRF」，不是「防火墙」。
    """
    try:
        # 已是 IP 就直接判断
        ipaddress.ip_address(host)
        return _is_private_ip(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    addrs = {info[4][0] for info in infos}
    return any(_is_private_ip(a) for a in addrs)


def validate_base_url(value: object, *, allow_private: bool | None = None) -> str:
    """校验模型服务地址：只允许 http/https，可选拦截内网地址（SSRF 防护）。"""
    s = str(value or "").strip()
    if not s:
        raise ValidationError("Base URL 不能为空")
    try:
        parsed = urlparse(s)
    except ValueError as exc:
        raise ValidationError("Base URL 不是合法的网址") from exc

    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Base URL 只支持 http:// 或 https:// 开头")
    if not parsed.hostname:
        raise ValidationError("Base URL 缺少主机名")

    allow = get_settings().allow_private_base_url if allow_private is None else allow_private
    if not allow and _hostname_resolves_to_private(parsed.hostname):
        raise ValidationError(
            "该地址指向内网/本机，出于安全已阻止。若确需接入内网模型，"
            "请设置 SOULMATE_ALLOW_PRIVATE_BASE_URL=true"
        )
    # 归一化：去掉末尾多余的斜杠（避免同一个地址因 / 数量不同被当成两条）
    return s.rstrip("/")


def validate_model_name(value: object) -> str:
    s = str(value or "").strip()
    if not s:
        raise ValidationError("模型名称不能为空")
    if len(s) > 200 or any(ch in s for ch in ("\n", "\r", "\x00")):
        raise ValidationError("模型名称不合法")
    return s
