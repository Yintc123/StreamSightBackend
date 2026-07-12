"""Unit tests for RedisCache helper (uses fakeredis)."""

import asyncio
from typing import Any

import pytest
import redis.asyncio as redis

from app.core.redis import RedisCache


async def test_set_and_get_dict(cache: RedisCache) -> None:
    key: str = "user:1"
    payload: dict[str, Any] = {"name": "alice", "age": 30}
    await cache.set(key, payload)

    result: Any = await cache.get(key)

    assert result == payload


async def test_get_missing_returns_none(cache: RedisCache) -> None:
    result: Any = await cache.get("nonexistent")

    assert result is None


async def test_set_without_ttl_persists(cache: RedisCache) -> None:
    """未設定 TTL 的 key 不過期"""
    key: str = "permanent"
    payload: dict = {"v": 1}
    await cache.set(key, payload)

    # 稍微 sleep 一下，確認沒過期
    await asyncio.sleep(0.05)

    assert await cache.get(key) == payload


async def test_delete_existing_returns_true(cache: RedisCache) -> None:
    key: str = "kill-me"
    payload: str = "value"

    await cache.set(key, payload)

    assert await cache.delete(key) is True
    assert await cache.get(key) is None


async def test_delete_missing_returns_false(cache: RedisCache) -> None:
    assert await cache.delete("never-existed") is False


async def test_exists_returns_true_when_set(cache: RedisCache) -> None:
    key: str = "here"
    payload: str = "value"

    await cache.set(key, payload)

    assert await cache.exists(key) is True


async def test_exists_returns_false_when_missing(cache: RedisCache) -> None:
    assert await cache.exists("not-here") is False


# parametrize 的機制：不是把整串 list 給 value，而是每個 element 都跑一次 test，一次一個 element。
@pytest.mark.parametrize(
    "value",
    [{"dict": "value"}, ["list", 1, True], "string", 42, True, None],
)
async def test_set_various_json_types(cache: RedisCache, value: Any) -> None:
    """RedisCache 應該支援所有 JSON native types 的 roundtrip。"""
    key: str = "k"
    await cache.set(key, value)

    assert await cache.get(key) == value


async def test_set_with_ttl_sets_expiry(cache: RedisCache, fake_redis: redis.Redis) -> None:
    """設 TTL 後，Redis 應該紀錄該 key 有過期時間。"""
    key: str = "temp"
    payload: str = "v"
    ttl: int = 60

    await cache.set(key, payload, ttl)

    # PTTL：回傳剩餘 ms，無 TTL 回 -1，不存在回 -2
    ttl_ms: int = await fake_redis.pttl(key)
    assert ttl_ms > 0  # key 還未過期，有 TTL


async def test_set_without_ttl_no_expiry(cache: RedisCache, fake_redis: redis.Redis) -> None:
    """未設 TTL 的 key，Redis 不紀錄過期時間"""
    key: str = "permanent"
    payload: str = "v"

    await cache.set(key, payload)

    ttl_ms: int = await fake_redis.pttl(key)
    assert ttl_ms == -1  # key 無設定 ttl 為 -1
