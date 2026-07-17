"""Production-faithful reuse detection test — commit-before-raise. Spec §8.5.

既有 `client` fixture 讓所有請求共用同一 db_session，且 override 版 get_session
不做 rollback-on-exception，故無法抓到「reuse 撤銷 family 卻沒 commit 就 raise」的 bug。

本檔用一個 production-faithful 的 get_session override：每請求獨立 session、
並複製正式 get_session 的 try/yield/except:rollback 語意。為了完全隔離、又能忠實地
「commit 真的落地、例外真的 rollback」，本檔建**專屬 engine**（獨立 in-memory DB），
測試結束整組 dispose，不干擾其他測試共用的 engine。
若 service 漏了 commit-before-raise，reuse 的 family 撤銷會被 except 的 rollback 回滾，
此測試即 RED。
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import redis.asyncio as redis
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_redis, get_session
from app.app import create_app
from app.core.auth import hash_refresh_token
from app.core.config import get_app_settings
from app.core.db import Base
from app.models import RefreshToken


@pytest.fixture
async def prod_client(
    fake_redis: redis.Redis,
) -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    """Client with a dedicated engine + production-faithful get_session.

    每請求開新 session、commit 真的落地、例外時 rollback（複製正式 get_session 語意）。
    專屬 engine → 與其他測試完全隔離，結束時 dispose。
    """
    engine = create_async_engine(
        get_app_settings().database_url,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection: Any, _record: Any) -> None:  # noqa: ANN401
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    app = create_app()

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        async with maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    def override_get_redis() -> redis.Redis:
        return fake_redis

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, maker

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_reuse_commit_before_raise_persists_family_revocation(
    prod_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, maker = prod_client
    monkeypatch.setattr(get_app_settings(), "refresh_token_reuse_grace_seconds", 0)

    reg: dict[str, Any] = (
        await client.post(
            "/auth/register",
            json={"email": "commit@example.com", "name": "C", "password": "longpassword"},
        )
    ).json()
    r1: str = reg["refresh_token"]
    r2: str = (await client.post("/auth/refresh", json={"refresh_token": r1})).json()[
        "refresh_token"
    ]

    # reuse 舊 token（超過 grace）→ 401，且撤 family 須已 commit
    reuse = await client.post("/auth/refresh", json={"refresh_token": r1})
    assert reuse.status_code == status.HTTP_401_UNAUTHORIZED

    # 在獨立 session 查詢：r2 的撤銷必須已落地（若沒 commit-before-raise 會是 None）
    async with maker() as verify:
        row = (
            await verify.execute(
                select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(r2))
            )
        ).scalar_one()
        assert row.revoked_at is not None

    # API 上 r2 也已失效（family 連坐）
    assert (
        await client.post("/auth/refresh", json={"refresh_token": r2})
    ).status_code == status.HTTP_401_UNAUTHORIZED
