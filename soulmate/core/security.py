"""安全原语：加密、口令哈希、签名令牌、密码强度。

【分三层，各管一件事】
- 加密（SecretBox）：把 API Key 这类「要能反解」的敏感值加密落盘。用 Fernet。
- 口令哈希（bcrypt）：把登录密码变成「只能验证、不能反解」的哈希。bcrypt 自带盐 + 慢速。
- 签名令牌：登录态。HMAC-SHA256 签名，服务端有 `app_secret` 才能签发/校验，防篡改。

【密钥从哪来】
Fernet key 和 HMAC key 都从 `app_secret` 用 PBKDF2 派生，**不要求用户再记一把新钥匙**。
生产环境 `app_secret` 必须显式设置且足够强（见 settings.validate_for_startup）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# 派生盐：固定值即可（盐的作用是让「相同输入→不同输出」这个指纹库无法复用，
# 不要求保密）。真正的秘密是 app_secret。
_DERIVE_SALT = b"soulmate-ai:v2:kdf-salt"

# ── 密钥派生 ────────────────────────────────────────────────────
def _derive_key(app_secret: str, purpose: str, length: int = 32) -> bytes:
    """从 app_secret 派生一把用途独立的子密钥。

    为什么要按用途分开：encrypt 和 sign 用同一把密钥是安全反模式，
    分开后即使一处泄露也不直接波及其它用途。
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=length,
        salt=_DERIVE_SALT + purpose.encode(),
        iterations=200_000,
    )
    return kdf.derive(app_secret.encode("utf-8"))


def _fernet_key(app_secret: str) -> bytes:
    """Fernet 需要 32 字节 urlsafe-base64 编码的密钥。"""
    return base64.urlsafe_b64encode(_derive_key(app_secret, "encrypt"))


def _hmac_key(app_secret: str) -> bytes:
    return _derive_key(app_secret, "sign")


# ── 对称加密 ────────────────────────────────────────────────────
class SecretBox:
    """用 Fernet 加密敏感字符串。同一进程内复用一个 box。"""

    def __init__(self, app_secret: str) -> None:
        self._fernet = Fernet(_fernet_key(app_secret))

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            # 密钥换过 / 数据被篡改 → 明确抛出，而不是返回空串掩盖问题
            raise ValueError("无法解密：密钥已变更或数据被篡改") from exc


# ── 口令哈希 ─────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """bcrypt 哈希。返回的字符串自带盐，可直接存库。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """校验口令。哈希格式非法也不抛异常，一律视为不匹配。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def password_strength(password: str) -> list[str]:
    """返回「不够强」的原因列表，空列表 = 达标。

    最小门槛 8 位。别设太高 —— 这是个人/小团队自托管，不是银行，
    过高的规则只会逼用户把密码写纸上。
    """
    problems: list[str] = []
    if len(password) < 8:
        problems.append("至少 8 个字符")
    if not any(c.islower() for c in password):
        problems.append("至少一个小写字母")
    if not any(c.isupper() for c in password):
        problems.append("至少一个大写字母")
    if not any(c.isdigit() for c in password):
        problems.append("至少一个数字")
    return problems


# ── 签名令牌（登录态）────────────────────────────────────────────
def sign_token(payload: dict[str, Any], app_secret: str, ttl_seconds: int) -> str:
    """签发带过期时间的签名令牌。格式：base64(payload).base64(sig)，点分隔。"""
    payload = dict(payload)
    payload["exp"] = int(time.time()) + int(ttl_seconds)
    body = base64.urlsafe_b64encode(
        __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_hmac_key(app_secret), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str, app_secret: str, ttl_seconds: int) -> dict[str, Any] | None:
    """校验并返回 payload；过期/被篡改返回 None。"""
    import json

    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_hmac_key(app_secret), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    # 校验有效期在签发 TTL 允许的合理范围内，防「改 exp 到无限远」的假令牌
    if payload.get("exp", 0) > int(time.time()) + int(ttl_seconds) + 5:
        return None
    return payload


def new_token_hex(nbytes: int = 16) -> str:
    """生成随机十六进制串（会话 ID 后缀等用途）。"""
    return secrets.token_hex(nbytes)
