import os

# 必須在任何 app import 之前設定，讓 get_app_settings 用 TestAppSettings
os.environ["APP_ENV"] = "test"
# Test 用 key（僅測試用，不用於任何真實資料）
os.environ["ENCRYPTION_KEY"] = "test-encryption-key-32-chars-min-length"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-32-chars-min-length-for-tests"
os.environ["REFRESH_TOKEN_HASH_SECRET"] = "test-refresh-token-pepper-32-chars-min-length"

from collections.abc import AsyncGenerator
from typing import Any

import fakeredis.aioredis
import pytest
import redis.asyncio as redis
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

from app.api.dependencies import get_redis, get_session
from app.app import create_app
from app.core.config import BaseAppSettings, get_app_settings
from app.core.db import Base
from app.core.redis import RedisCache
from app.dtos import UserCreate, UserUpdate
from app.models.admin import Admin
from app.models.user import User
from app.services import AdminService, UserService
from tests.payloads import user_payload

# 測試用初始 admin 憑證（僅測試，不用於真實資料）
ADMIN_EMAIL: str = "admin@example.com"
ADMIN_PASSWORD: str = "admin-longpassword"


# ────────────────────────────────────────────────
# Session-scoped engine (只建立一次，整個 test session 共用)
# ────────────────────────────────────────────────
@pytest.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine]:
    """Async engine for the test session. Creates all tables once.

    SQLite `:memory:` 每個 connection 是獨立 DB。用 StaticPool 讓整個
    session (整個 pytest 共用 engine) 共用同一 connection，才能看到彼此的資料。
    """
    settings: BaseAppSettings = get_app_settings()

    engine_kwargs: dict[str, Any] = {"echo": False}
    if settings.database_url.startswith("sqlite"):
        engine_kwargs["poolclass"] = StaticPool
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    engine: AsyncEngine = create_async_engine(settings.database_url, **engine_kwargs)

    # SQLite 預設關閉 FK enforcement、開起來讓 CASCADE / UniqueConstraint
    # 真的執行(和 Postgres 行為對齊、才測得到 real behavior)
    if settings.database_url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_connection: Any, _record: Any) -> None:  # noqa: ANN401
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

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
async def client(
    db_session: AsyncSession,
    fake_redis: redis.Redis,
) -> AsyncGenerator[AsyncClient]:
    """AsyncClient that shares the test's db_session + fake_redis via dependency_overrides."""
    app: FastAPI = create_app()

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    def override_get_redis() -> redis.Redis:
        return fake_redis

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_redis] = override_get_redis

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


@pytest.fixture
async def admin(db_session: AsyncSession) -> Admin:
    """Pre-created CMS admin (role=1) via AdminService (seed-equivalent)."""
    service: AdminService = AdminService(db_session)
    return await service.create(email=ADMIN_EMAIL, name="Root", password=ADMIN_PASSWORD)


# ────────────────────────────────────────────────
# Fake Redis fixtures（純 Python in-memory、每 test 乾淨）
# ────────────────────────────────────────────────
@pytest.fixture(scope="function")
async def fake_redis() -> AsyncGenerator[redis.Redis]:
    """
    Fake Redis client using fakeredis.aioredis. Isolated per test.
    function scope：每個 test 後都會執行 await client.aclose()，每個 test 完全隔離。

    fakeredis 建 instance 成本極低（純 in-memory dict，僅需要幾 microseconds），每一個 test 建一次無幾乎成本
    """
    client: redis.Redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def cache(fake_redis: redis.Redis) -> RedisCache:
    """RedisCache backed by the per-test fake_redis client."""
    return RedisCache(fake_redis)
