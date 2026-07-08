from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_app_settings, BaseAppSettings

def _create_engine() -> AsyncEngine:
    """Create the async engine with setting-driven config."""
    settings: BaseAppSettings = get_app_settings()

    # SQLite 用 NullPool，不吃 pool_size/pool_recycle 參數
    engine_kwargs: dict = {"echo": settings.database_echo}
    if not settings.database_url.startswith("sqlite"):
        engine_kwargs["pool_size"] = settings.database_pool_size
        engine_kwargs["pool_recycle"] = settings.database_pool_recycle
    
    return create_async_engine(settings.database_url, **engine_kwargs)

engine: AsyncEngine = _create_engine()

# async_sessionmaker 為 factory-like class 用小寫
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False, # commit 後仍能存取 model attributes
    autoflush=False,        # 明確控制 flush 時機，避免非預期 SQL
)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: yield an AsyncSession, rollback on error.

    使用方式：
        @router.get("/users")
        async def list_users(db: AsyncSession = Depends(get_session)):
            result = await db.execute(select(User))
            return result.scalars().all()
    """
    # 建立 session (lazy — 首次執行 SQL 時才從 pool 借 connection)
    async with AsyncSessionLocal() as session:
        try:
            # 將 session 交給 endpoint 使用
            yield session
        except Exception:
            await session.rollback()
            raise