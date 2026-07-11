import os

# 必須在任何 app import 之前設定，讓 get_app_settings 用 TestAppSettings
os.environ["APP_ENV"] = "test"
# Test 用 key（僅測試用，不用於任何真實資料）
os.environ["ENCRYPTION_KEY"] = "test-encryption-key-32-chars-min-length"

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    AsyncTransaction,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.app import create_app
from app.core.config import BaseAppSettings, get_app_settings
from app.core.db import Base, get_session
from app.dtos import UserCreate, UserUpdate
from app.models.user import User
from app.services import UserService
from tests.payloads import user_payload


# ────────────────────────────────────────────────
# Session-scoped engine (只建立一次，整個 test session 共用)
# ────────────────────────────────────────────────
@pytest.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine]:
    """Async engine for the test session. Creates all tables once.

    SQLite `:memory:` 每個 connection 是獨立 DB。用 StaticPool 讓所有
    session 共用同一 connection，才能看到彼此的資料。
    """
    settings: BaseAppSettings = get_app_settings()

    engine_kwargs: dict[str, Any] = {"echo": False}
    if settings.database_url.startswith("sqlite"):
        engine_kwargs["poolclass"] = StaticPool
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    engine: AsyncEngine = create_async_engine(settings.database_url, **engine_kwargs)

    async with engine.begin() as conn:
        # 建立所有 SQLAlchemy 定義的 tables
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ────────────────────────────────────────────────
# Per-test session (每個 test 一個乾淨的 session，自動  rollback)
# ────────────────────────────────────────────────
@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Per-test AsyncSession that rolls back to keep tests isolated."""
    connection: AsyncConnection = await engine.connect()
    transaction: AsyncTransaction = await connection.begin()

    session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
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
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """AsyncClient that shares the test's db_session via dependency_overrides."""
    app: FastAPI = create_app()

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    transport: ASGITransport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ────────────────────────────────────────────────
# Pre-populated user fixtures (rollback per test 保證隔離)
# ────────────────────────────────────────────────
@pytest.fixture
async def alice(db_session: AsyncSession) -> User:
    """Pre-created 'Alice' user (from tests/data/users.py)."""
    service: UserService = UserService(db_session)
    return await service.create(UserCreate(**user_payload("alice")))


@pytest.fixture
async def bob(db_session: AsyncSession) -> User:
    """Pre-created 'Bob' user (from tests/data/users.py)."""
    service: UserService = UserService(db_session)
    return await service.create(UserCreate(**user_payload("bob")))


@pytest.fixture
async def sample_users(db_session: AsyncSession) -> list[User]:
    """Pre-created 3 users with unique auto-generated emails."""
    service: UserService = UserService(db_session)
    return [
        await service.create(
            UserCreate(
                email=f"sample{i}@example.com",
                name=f"Sample {i}",
            )
        )
        for i in range(3)
    ]


@pytest.fixture
async def inactive_user(db_session: AsyncSession, alice: User) -> User:
    """Alice, but deactivated (is_active=False)."""
    service: UserService = UserService(db_session)
    return await service.update(alice.id, UserUpdate(is_active=False))
