"""Unit tests for password hashing helpers (async, via asyncio.to_thread)."""

import pytest
from argon2.exceptions import InvalidHashError

from app.core.auth import hash_password, verify_password

my_secret: str = "mysecret"


async def test_hash_produces_argon2id_output() -> None:
    result: str = await hash_password(my_secret)

    # argon2 hash 格式：$argon2id$v=19$m=...$<salt>$<hash>
    assert result.startswith("$argon2id$")


async def test_hash_is_non_deterministic() -> None:
    """同密碼 hash 兩次應該不同 - 因為 salt 隨機。"""
    h1: str = await hash_password(my_secret)
    h2: str = await hash_password(my_secret)

    assert h1 != h2


async def test_verify_correct_password_returns_true() -> None:
    hashed: str = await hash_password(my_secret)

    assert await verify_password(my_secret, hashed) is True


async def test_verify_wrong_password_returns_false() -> None:
    """密碼錯誤應回 False 並且不 raise"""
    hashed: str = await hash_password("correct")

    assert await verify_password("wrong", hashed) is False


async def test_verify_invalid_hash_raises() -> None:
    """錯誤的 hash 字串應該 raise InvalidHashError (不是 return False)"""
    with pytest.raises(InvalidHashError):
        await verify_password(my_secret, "not-a-real-hash")
