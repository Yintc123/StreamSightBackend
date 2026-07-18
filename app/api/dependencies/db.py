from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import AsyncSessionLocal


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """FastAPI dependency: 回 app 級 async session 工廠（WS 長連線用，§2.2/§4）。

    與 `get_session` **並存**。WS 端點與定期複查 task 不用 `Depends(get_session)`
    （避免 request-scoped session 綁死無上限的連線壽命，且多個並發 task 共用單一
    `AsyncSession` 並發不安全）；改以此工廠，每個 DB 工作單元開短命 session、用畢即還，
    連線閒置期間不持有任何 DB connection。
    """
    return AsyncSessionLocal


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
