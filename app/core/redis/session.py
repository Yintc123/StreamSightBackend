import redis.asyncio as redis
from app.core.config import BaseAppSettings, get_app_settings


def _create_redis_client() -> redis.Redis:
    """
    Build the Redis async client from settings.

    - `Redis.from_url()` 內含連線池，不會立即連線 (lazy on first command)
    - `max_connections`： pool 上限
    - `decode_responses=True`： server 回傳 bytes 時自動 decode 成 str
    """
    settings: BaseAppSettings = get_app_settings()
    return redis.Redis.from_url(
        settings.redis_url,
        max_connections=settings.redis_pool_max_connections,
        # 改成過一個 helper !important
        decode_responses=True,
    )


# module-level singleton
redis_client: redis.Redis = _create_redis_client()


async def close_redis() -> None:
    """Close the Redis client pool. Called from app lifespan shutdown."""
    await redis_client.aclose()
