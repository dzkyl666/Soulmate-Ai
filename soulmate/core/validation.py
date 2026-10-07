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


# 真正属于「内网 / 本机 / 不可路由」的网段。
#
# 为什么不直接用 `ipaddress.is_private`：它把 IANA 的「非全球可达」特殊段
# 也算成 private，其中两段会造成**正常公网厂商被误拦**（实测证据见 docs/SECURITY.md）：
#
#   · 2001::/23 —— 含 Teredo(2001::/32) 与基准测试段。
#     实测 `api.openai.com` 的 AAAA 记录 `2001::c73b:9466` 正落在里面，
#     于是「OpenAI 官方」这个预设会被本项目的 SSRF 检查拦掉。
#   · 198.18.0.0/15 —— 基准测试段，同时也是代理软件 fake-IP 模式的常用映射段。
#     它是「代理产物」，不是内网服务；若按内网拦，开 fake-IP 的用户全都配不了模型。
#
# 所以这里**显式列出**真正的内网段 —— 宁可写清楚，也不复用语义过宽的 is_private。
_INTERNAL_NETWORKS_V4 = (
    ipaddress.ip_network("0.0.0.0/8"),        # 本网络
    ipaddress.ip_network("10.0.0.0/8"),       # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),    # 运营商级 NAT
    ipaddress.ip_network("127.0.0.0/8"),      # 回环
    ipaddress.ip_network("169.254.0.0/16"),   # 链路本地
    ipaddress.ip_network("172.16.0.0/12"),    # RFC1918
    ipaddress.ip_network("192.0.0.0/24"),     # IETF 协议分配
    ipaddress.ip_network("192.168.0.0/16"),   # RFC1918
    ipaddress.ip_network("224.0.0.0/4"),      # 组播
    ipaddress.ip_network("240.0.0.0/4"),      # 保留
)

_INTERNAL_NETWORKS_V6 = (
    ipaddress.ip_network("::/128"),           # 未指定
    ipaddress.ip_network("::1/128"),          # 回环
    ipaddress.ip_network("fc00::/7"),         # 唯一本地地址（ULA）
    ipaddress.ip_network("fe80::/10"),        # 链路本地
    ipaddress.ip_network("ff00::/8"),         # 组播
)


def is_internal_address(value: str) -> bool:
    """判断一个 IP 字面量是否属于**真正的内网/本机**地址。

    IPv4-mapped IPv6（如 `::ffff:127.0.0.1`）会解出内嵌的 IPv4 再判断 ——
    否则攻击者可以用这种写法绕过检查。
    """
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return is_internal_address(str(mapped))
        return any(ip in net for net in _INTERNAL_NETWORKS_V6)
    return any(ip in net for net in _INTERNAL_NETWORKS_V4)


def is_ip_literal(value: str) -> bool:
    """`value` 是不是 IP 字面量（而不是域名）。

    独立成函数而不是写成 `try/except/pass`：本项目的 verify.py 会用 AST
    禁止「静默吞异常的 except: pass」，而那种写法正好命中（写的时候就被自己的检查抓过）。
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def resolved_addresses(host: str) -> list[str]:
    """把主机名解析成 IP 列表；host 本身是 IP 字面量时直接返回它。

    解析失败返回空列表（调用方据此 fail-open，见 validate_base_url 的说明）。
    """
    if is_ip_literal(host):
        return [host]
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError):
        return []
    return sorted({str(info[4][0]) for info in infos})


def _hostname_resolves_to_internal(host: str) -> list[str]:
    """返回该主机名解析出的**内网地址**列表（空列表 = 安全）。

    只要解析结果里**存在**内网地址就判定为不安全（宁可严一点）——
    因为客户端可能挑中那一个去连接。
    定位：挡住「明显的 SSRF」，不是防火墙；解析失败时 fail-open。
    """
    return [a for a in resolved_addresses(host) if is_internal_address(a)]


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
    hostname = str(parsed.hostname or "")
    if not hostname:
        raise ValidationError("Base URL 缺少主机名")

    allow = get_settings().allow_private_base_url if allow_private is None else allow_private
    if not allow:
        internal = _hostname_resolves_to_internal(hostname)
        if internal:
            # 错误文案里带上解析结果：用户填的是公网域名却被拦时，
            # 只有看到"解析到了什么"才能判断是自己网络的问题还是配置的问题。
            shown = ", ".join(internal[:3])
            raise ValidationError(
                f"「{hostname}」解析到内网/本机地址（{shown}），出于安全已阻止。"
                "如果你确实要接内网模型（如 Ollama / 内网反代），"
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
