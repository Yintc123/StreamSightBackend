from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal


async def get_session() -> AsyncGenerator[AsyncSession]:
    """
    FastAPI dependency: yield an AsyncSession, rollback on error.

    使用方式：
        @router.get("/users")
        async def list_users(db: AsyncSession = Depends(get_session)):
            result = await db.execute(select(User))
            return result.scalars().all()

    非 FastAPI 場景（CLI / worker）直接用 `AsyncSessionLocal()` context manager。
    """
    # 建立 session (lazy — 首次執行 SQL 時才從 pool 借 connection)
    async with AsyncSessionLocal() as session:
        try:
            # 將 session 交給 endpoint 使用
            yield session
        except Exception:
            await session.rollback()
            raise
