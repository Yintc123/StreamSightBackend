import redis.asyncio as redis
from fastapi import Depends

from app.core.redis import RedisCache, redis_client


def get_redis() -> redis.Redis:
    """FastAPI dependency: return the shared Redis client."""
    return redis_client


def get_cache(client: redis.Redis = Depends(get_redis)) -> RedisCache:
    """FastAPI dependency: build a RedisCache bound to the shared client."""
    return RedisCache(client)
