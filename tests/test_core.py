"""core 层测试：异常脱敏 / 校验 / 加解密 / 口令 / 令牌 / 限流。"""

from __future__ import annotations

import time

import pytest

from soulmate.core import validation
from soulmate.core.exceptions import (
    ProviderAuthError,
    ProviderError,
    RateLimitError,
    SoulmateError,
    ValidationError,
    mask_secret,
    redact,
)
from soulmate.core.ratelimit import RateLimiter
from soulmate.core.security import (
    SecretBox,
    hash_password,
    password_strength,
    sign_token,
    verify_password,
    verify_token,
)

SECRET = "s" * 48


# ══════════════════════════════════════════════════════════
# 脱敏
# ══════════════════════════════════════════════════════════
class TestRedact:
    def test_masks_openai_style_key(self):
        out = redact("调用失败 key=sk-abcdefghijklmnop")
        assert "sk-abcdefghijklmnop" not in out
        assert "<redacted>" in out

    def test_masks_bearer_token(self):
        out = redact("headers={'Authorization': 'Bearer abcdefghijklmnop'}")
        assert "abcdefghijklmnop" not in out

    def test_masks_url_credentials(self):
        out = redact("https://alice:supersecret@example.com/v1")
        assert "supersecret" not in out
        assert "example.com" in out

    def test_masks_labelled_secret(self):
        out = redact("api_key=verylongsecretvalue token: anothersecret")
        assert "verylongsecretvalue" not in out
        assert "anothersecret" not in out

    def test_keeps_ordinary_text(self):
        assert redact("普通文本没有密钥") == "普通文本没有密钥"

    def test_mask_secret_keeps_tail(self):
        assert mask_secret("sk-1234567890abcd").endswith("abcd")
        assert "sk-1234567890" not in mask_secret("sk-1234567890abcd")
        assert mask_secret("") == ""


# ══════════════════════════════════════════════════════════
# 异常
# ══════════════════════════════════════════════════════════
class TestExceptions:
    def test_user_message_never_leaks_key(self):
        exc = ProviderAuthError("失败 sk-abcdefghijklmnop", details={"base_url": "http://x"})
        assert "sk-abcdefghijklmnop" not in exc.user_message()

    def test_provider_error_instance_retryable_overrides_class(self):
        assert ProviderError("x").retryable is False
        assert ProviderError("x", retryable=True).retryable is True
        assert ProviderError("x", retryable=True).details["retryable"] is True

    def test_rate_limit_carries_retry_after(self):
        exc = RateLimitError("太快", retry_after=7.5)
        assert exc.retry_after == 7.5
        assert "7" in exc.user_message() or "8" in exc.user_message()

    def test_to_dict_is_structured(self):
        d = SoulmateError("boom", details={"a": 1}).to_dict()
        assert d["code"] and d["message"] and d["details"]["a"] == 1


# ══════════════════════════════════════════════════════════
# 校验
# ══════════════════════════════════════════════════════════
class TestValidateId:
    @pytest.mark.parametrize("bad", ["", "   ", "../etc/passwd", "a/b", "a\\b", "a\x00b", "x" * 200])
    def test_rejects_dangerous_ids(self, bad):
        with pytest.raises(ValidationError):
            validation.validate_id(bad)

    @pytest.mark.parametrize("good", ["abc", "a-1_b.2", "2026-09-10_19-38-45_deadbeef"])
    def test_accepts_safe_ids(self, good):
        assert validation.validate_id(good) == good


class TestValidateBaseUrl:
    def test_accepts_https_and_strips_trailing_slash(self):
        assert validation.validate_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"

    @pytest.mark.parametrize("bad", ["", "   ", "file:///etc/passwd", "ftp://x.com", "notaurl", "https://"])
    def test_rejects_bad_urls(self, bad):
        with pytest.raises(ValidationError):
            validation.validate_base_url(bad)

    def test_blocks_loopback_by_default(self):
        with pytest.raises(ValidationError):
            validation.validate_base_url("http://127.0.0.1:8000/v1", allow_private=False)

    def test_allows_private_when_explicitly_enabled(self):
        assert validation.validate_base_url("http://127.0.0.1:8000/v1", allow_private=True)

    def test_blocks_hostname_that_resolves_to_internal(self, monkeypatch):
        """域名（不是 IP 字面量）解析到内网时也要拦 —— 这才是 SSRF 的真实形态。"""
        monkeypatch.setattr("soulmate.core.validation.resolved_addresses", lambda host: ["10.0.0.5"])
        with pytest.raises(ValidationError) as ei:
            validation.validate_base_url("https://internal.example.com/v1", allow_private=False)
        assert "10.0.0.5" in str(ei.value)  # 报错要带上解析结果，用户才知道为什么被拦

    def test_blocks_when_any_resolved_address_is_internal(self, monkeypatch):
        """一个域名返回多个地址时，只要有内网就拦（客户端可能挑中那一个）。"""
        monkeypatch.setattr(
            "soulmate.core.validation.resolved_addresses",
            lambda host: ["93.184.216.34", "192.168.1.10"],
        )
        with pytest.raises(ValidationError):
            validation.validate_base_url("https://mixed.example.com/v1", allow_private=False)

    def test_fail_open_when_dns_unresolvable(self, monkeypatch):
        """离线/DNS 失败时放行（否则用户在断网环境连本地都配不了）。"""
        monkeypatch.setattr("soulmate.core.validation.resolved_addresses", lambda host: [])
        assert validation.validate_base_url("https://nowhere.invalid/v1", allow_private=False)


class TestInternalAddressPredicate:
    """★ 回归测试：修掉「正常公网厂商被 SSRF 检查误拦」的真实缺陷。

    根因：原先直接用 `ipaddress.is_private`，而它把 IANA 的
    「非全球可达」特殊段也算私有，其中 `2001::/23`（Teredo + 基准测试）
    覆盖了 `api.openai.com` 的 AAAA 记录 `2001::c73b:9466`
    —— 于是「OpenAI 官方」预设会被自己的安全检查拦掉。
    """

    @pytest.mark.parametrize(
        "addr",
        [
            "2001::c73b:9466",   # Teredo 段：api.openai.com 的真实 AAAA 记录
            "198.18.0.14",       # 基准测试段 / 代理 fake-IP 常用映射
            "108.160.169.175",   # 普通公网 IPv4
            "93.184.216.34",
            "2606:4700:4700::1111",  # 公网 IPv6（Cloudflare DNS）
        ],
    )
    def test_public_addresses_are_not_internal(self, addr):
        assert validation.is_internal_address(addr) is False

    @pytest.mark.parametrize(
        ("addr", "why"),
        [
            ("10.0.0.5", "RFC1918"),
            ("172.16.3.9", "RFC1918"),
            ("192.168.1.1", "RFC1918"),
            ("127.0.0.1", "回环"),
            ("169.254.1.1", "链路本地"),
            ("100.64.0.1", "运营商级 NAT"),
            ("0.0.0.0", "本网络"),
            ("::1", "IPv6 回环"),
            ("fc00::1", "IPv6 ULA"),
            ("fe80::1", "IPv6 链路本地"),
        ],
    )
    def test_internal_addresses_are_internal(self, addr, why):
        assert validation.is_internal_address(addr) is True, why

    def test_ipv4_mapped_ipv6_cannot_bypass(self):
        """`::ffff:127.0.0.1` 这种写法必须被识破，否则就是一条绕过路径。"""
        assert validation.is_internal_address("::ffff:127.0.0.1") is True
        assert validation.is_internal_address("::ffff:10.1.2.3") is True

    def test_non_ip_string_is_not_internal(self):
        assert validation.is_internal_address("not-an-ip") is False

    def test_openai_preset_url_is_accepted(self):
        """把预设表真跑一遍：所有内置厂商地址都必须能通过校验。

        这条能挡住「新增预设时忘了它会被自己的安全检查拦掉」这类回归。
        """
        from soulmate.core.presets import PROVIDER_PRESETS

        for key, preset in PROVIDER_PRESETS.items():
            url = str(preset.get("base_url") or "")
            if not url:
                continue  # custom 预设本来就是空的
            # 用 allow_private=True 跳过 DNS 分支，专测 scheme/host 解析层
            assert validation.validate_base_url(url, allow_private=True), key


class TestValidateOther:
    def test_username_normalises_and_validates(self):
        assert validation.validate_username("  Alice_01 ") == "alice_01"
        with pytest.raises(ValidationError):
            validation.validate_username("ab")  # 太短
        with pytest.raises(ValidationError):
            validation.validate_username("has space")

    def test_text_max_chars(self):
        assert validation.validate_text("  hi  ", field="消息", max_chars=10) == "hi"
        with pytest.raises(ValidationError):
            validation.validate_text("x" * 11, field="消息", max_chars=10)
        with pytest.raises(ValidationError):
            validation.validate_text("", field="消息", max_chars=10, allow_empty=False)

    def test_model_name_rejects_newlines(self):
        with pytest.raises(ValidationError):
            validation.validate_model_name("gpt\n4o")
        assert validation.validate_model_name("gpt-4o") == "gpt-4o"

    def test_avatar_length(self):
        assert validation.validate_avatar("🧸") == "🧸"
        with pytest.raises(ValidationError):
            validation.validate_avatar("x" * 20)


# ══════════════════════════════════════════════════════════
# 加解密 / 口令 / 令牌
# ══════════════════════════════════════════════════════════
class TestSecretBox:
    def test_roundtrip(self):
        box = SecretBox(SECRET)
        token = box.encrypt("sk-secret-value")
        assert token != "sk-secret-value"
        assert box.decrypt(token) == "sk-secret-value"

    def test_roundtrip_unicode(self):
        box = SecretBox(SECRET)
        assert box.decrypt(box.encrypt("中文密钥🔑")) == "中文密钥🔑"

    def test_empty_passthrough(self):
        box = SecretBox(SECRET)
        assert box.encrypt("") == ""
        assert box.decrypt("") == ""

    def test_tampered_token_raises(self):
        box = SecretBox(SECRET)
        token = box.encrypt("value")
        with pytest.raises(ValueError):
            box.decrypt(token[:-4] + "AAAA")

    def test_different_secret_cannot_decrypt(self):
        token = SecretBox(SECRET).encrypt("value")
        with pytest.raises(ValueError):
            SecretBox("z" * 48).decrypt(token)


class TestPasswords:
    def test_hash_and_verify(self):
        h = hash_password("Passw0rd123")
        assert h != "Passw0rd123"
        assert verify_password("Passw0rd123", h)
        assert not verify_password("wrong", h)

    def test_verify_survives_garbage_hash(self):
        assert not verify_password("x", "not-a-bcrypt-hash")

    def test_strength_rules(self):
        assert password_strength("Passw0rd123") == []
        assert password_strength("short") != []
        assert password_strength("alllowercase1") != []
        assert password_strength("ALLUPPERCASE1") != []
        assert password_strength("NoDigitsHere") != []


class TestTokens:
    def test_roundtrip(self):
        token = sign_token({"u": "alice"}, SECRET, ttl_seconds=60)
        assert verify_token(token, SECRET, ttl_seconds=60)["u"] == "alice"

    def test_wrong_secret_rejected(self):
        token = sign_token({"u": "alice"}, SECRET, ttl_seconds=60)
        assert verify_token(token, "z" * 48, ttl_seconds=60) is None

    def test_tampered_payload_rejected(self):
        token = sign_token({"u": "alice"}, SECRET, ttl_seconds=60)
        body, sig = token.rsplit(".", 1)
        assert verify_token(f"{body}x.{sig}", SECRET, ttl_seconds=60) is None

    def test_expired_rejected(self):
        token = sign_token({"u": "alice"}, SECRET, ttl_seconds=-1)
        assert verify_token(token, SECRET, ttl_seconds=60) is None

    def test_garbage_rejected(self):
        for bad in ["", "x", "a.b.c", "....."]:
            assert verify_token(bad, SECRET, ttl_seconds=60) is None


# ══════════════════════════════════════════════════════════
# 限流
# ══════════════════════════════════════════════════════════
class TestRateLimiter:
    def test_allows_up_to_limit_then_raises(self):
        rl = RateLimiter()
        for _ in range(3):
            rl.hit("k", limit=3, per_seconds=60)
        with pytest.raises(RateLimitError) as ei:
            rl.hit("k", limit=3, per_seconds=60)
        assert ei.value.retry_after > 0

    def test_keys_are_isolated(self):
        rl = RateLimiter()
        rl.hit("a", limit=1, per_seconds=60)
        rl.hit("b", limit=1, per_seconds=60)  # 不受 a 影响

    def test_window_expiry_frees_quota(self):
        rl = RateLimiter()
        rl.hit("k", limit=1, per_seconds=1)
        time.sleep(1.05)
        rl.hit("k", limit=1, per_seconds=1)  # 旧记录过期，应放行

    def test_remaining_and_reset(self):
        rl = RateLimiter()
        rl.hit("k", limit=5, per_seconds=60)
        assert rl.remaining("k", limit=5, per_seconds=60) == 4
        rl.reset("k")
        assert rl.remaining("k", limit=5, per_seconds=60) == 5
