import os
from typing import Any

# 必須在任何 app import 之前設定，讓 get_app_settings 用 TestAppSettings
os.environ["APP_ENV"] = "test"

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker
)

from app.app import create_app
from app.core.config import get_app_settings, TestAppSettings
from app.core.db import Base, get_session

# ────────────────────────────────────────────────
# Session-scoped engine (只建立一次，整個 test session 共用)
# ────────────────────────────────────────────────
@pytest.fixture(scope="session")
async def engine() -> Any | None:
    """Async engine for the test session. Creates all tables once."""
    settings: TestAppSettings = get_app_settings()
    # settings.database_echo 有設定值，為什麼不用？ 要寫死
    engine: Any = create_async_engine(settings.database_url, echo=False)
    
    async with engine.begin() as conn:
        await conn.run.sync(Base.metadata.create_all)

    # 為什麼是在 code block 將 engine 返回？這樣連線不就關了嗎？ with 會把連線關閉
    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

# ────────────────────────────────────────────────
# Per-test session (每個 test 一個乾淨的 session，自動  rollback)
# ────────────────────────────────────────────────
@pytest.fixture
async def db_session(engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """Per-test AsyncSession that rolls back to keep tests isolated."""
    connection: Any = await engine.connect()
    transaction: Any = await connection.begin()

    session_maker: Any = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    session: AsyncSession = session_maker()

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()

# ────────────────────────────────────────────────
# HTTP client with dependency override
# ────────────────────────────────────────────────
@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient that shares the test's db_session via dependency_overrides."""
    app: Any = create_app()

    async def override_get_session():
        yield db_session

    # 用測試用的 session 取代嗎？
    app.dependency_overrides[get_session] = override_get_session

    transport: Any = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()