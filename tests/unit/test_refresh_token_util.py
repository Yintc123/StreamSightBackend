"""Unit tests for refresh token util (generate + HMAC hash). Spec §8.1."""

import re
import string

import pytest
from pydantic import SecretStr

from app.core.auth import generate_refresh_token, hash_refresh_token

_URL_SAFE: set[str] = set(string.ascii_letters + string.digits + "-_")
_HEX_RE: re.Pattern[str] = re.compile(r"\A[0-9a-f]{64}\Z")


def test_generate_returns_distinct_tokens() -> None:
    assert generate_refresh_token() != generate_refresh_token()


def test_generate_is_url_safe_and_long_enough() -> None:
    token: str = generate_refresh_token()
    # token_urlsafe(32) 產生約 43 字元的 URL-safe 字串
    assert len(token) >= 32
    assert set(token) <= _URL_SAFE


def test_hash_is_deterministic() -> None:
    token: str = generate_refresh_token()
    assert hash_refresh_token(token) == hash_refresh_token(token)


def test_hash_differs_for_different_input() -> None:
    assert hash_refresh_token("token-a") != hash_refresh_token("token-b")


def test_hash_is_64_char_hex() -> None:
    digest: str = hash_refresh_token(generate_refresh_token())
    assert _HEX_RE.match(digest)


def test_hash_not_equal_plaintext() -> None:
    token: str = generate_refresh_token()
    assert hash_refresh_token(token) != token


def test_hash_is_keyed_by_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    """HMAC（非裸 SHA-256）：換 pepper → 同一 token 的 hash 改變。"""
    token: str = "same-token"

    class _Settings:
        def __init__(self, pepper: str) -> None:
            self.refresh_token_hash_secret = SecretStr(pepper)

    monkeypatch.setattr("app.core.auth.refresh.get_app_settings", lambda: _Settings("pepper-a"))
    hash_a: str = hash_refresh_token(token)

    monkeypatch.setattr("app.core.auth.refresh.get_app_settings", lambda: _Settings("pepper-b"))
    hash_b: str = hash_refresh_token(token)

    assert hash_a != hash_b
