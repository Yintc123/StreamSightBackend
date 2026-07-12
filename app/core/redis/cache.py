"""
Redis cache wrapper class - JSON-based get/set with optional TTL.
wrapper： JSON 序列化 + TTL
"""

import json
from typing import Any

import redis.asyncio as redis


class RedisCache:
    """
    JSON-serializing cache wrapper over a Redis client.

    使用方式：
        cache = RedisCache(redis_client)
        await cache.set("user:1", {"name": "alice"}, ttl=3600)
        data = await cache.get("user:1") # 得到 {'name': 'alice'}
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client: redis.Redis = client

    async def get(self, key: str) -> Any | None:
        """Get and JSON-decode. Returns None if key not found."""
        raw: str | bytes | None = await self._client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    # set 字串
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """JSON-encode and set. `ttl` in seconds; None = no expiry."""
        payload: str = json.dumps(value)
        await self._client.set(key, payload, ex=ttl)

    async def delete(self, key: str) -> bool:
        """Delete key. Returns True if key existed and was deleted."""
        deleted: int = await self._client.delete(key)
        return deleted > 0

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        count: int = await self._client.exists(key)
        return count > 0
