from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import BaseAppSettings, get_app_settings


def _create_engine() -> AsyncEngine:
    """Create the async engine with setting-driven config."""
    settings: BaseAppSettings = get_app_settings()

    # SQLite 用 NullPool，不吃 pool_size/pool_recycle 參數
    engine_kwargs: dict[str, Any] = {"echo": settings.database_echo}
    if not settings.database_url.startswith("sqlite"):
        engine_kwargs["pool_size"] = settings.database_pool_size
        engine_kwargs["pool_recycle"] = settings.database_pool_recycle

    return create_async_engine(settings.database_url, **engine_kwargs)


engine: AsyncEngine = _create_engine()

# async_sessionmaker 為 factory-like class 用小寫
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # commit 後仍能存取 model attributes
    autoflush=False,  # 明確控制 flush 時機，避免非預期 SQL
)
