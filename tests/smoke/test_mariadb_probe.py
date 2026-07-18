"""MariaDbStatsProbe 真實 MariaDB 煙霧測試（monitoring.md §8 step 9）。

本測試直接建立 MariaDB 引擎（不走 conftest SQLite fixture），
驗 SHOW GLOBAL STATUS / information_schema.PROCESSLIST / TABLES 查詢可正常回應。

執行：
    uv run pytest tests/smoke/test_mariadb_probe.py -v
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.services.monitoring.db_probe import MariaDbStatsProbe, PoolStatsProbe

MARIADB_URL = "mysql+asyncmy://streamsight:streamsight@localhost:3306/streamsight"
DB_NAME = "streamsight"


@pytest.fixture(scope="module")
async def mariadb_engine() -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(MARIADB_URL, echo=False, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
def mariadb_session_factory(mariadb_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(mariadb_engine, expire_on_commit=False)


@pytest.fixture(scope="module")
def probe(
    mariadb_engine: AsyncEngine, mariadb_session_factory: async_sessionmaker[AsyncSession]
) -> MariaDbStatsProbe:
    return MariaDbStatsProbe(mariadb_engine, mariadb_session_factory, DB_NAME)


async def test_pool_stats_non_zero(mariadb_engine: AsyncEngine) -> None:
    """PoolStatsProbe 在真實引擎上回有效數值（非 StaticPool）。"""
    p = PoolStatsProbe(mariadb_engine)
    data = await p.sample()
    assert data["backend"] == "pool_only"
    pool = data["pool"]
    assert isinstance(pool["size"], int)
    assert isinstance(pool["checked_out"], int)


async def test_mariadb_probe_structure(probe: MariaDbStatsProbe) -> None:
    """MariaDbStatsProbe.sample() 回正確頂層結構（monitoring.md §3.2）。"""
    data = await probe.sample()
    assert data["backend"] == "mariadb"
    assert isinstance(data["ts"], int) and data["ts"] > 0
    assert isinstance(data["pool"], dict)
    assert isinstance(data["connections"], dict)


async def test_mariadb_connections_fields(probe: MariaDbStatsProbe) -> None:
    """connections 包含 connected / running / idle（THREADS_CONNECTED / THREADS_RUNNING）。"""
    data = await probe.sample()
    conns = data["connections"]
    assert "connected" in conns
    assert "running" in conns
    assert "idle" in conns
    assert conns["connected"] >= 0
    assert conns["running"] >= 0
    assert conns["idle"] >= 0


async def test_mariadb_db_size_bytes(probe: MariaDbStatsProbe) -> None:
    """db_size_bytes 非 None（information_schema.TABLES 有資料）。"""
    data = await probe.sample()
    assert data["db_size_bytes"] is not None
    assert isinstance(data["db_size_bytes"], int)
    assert data["db_size_bytes"] >= 0


async def test_mariadb_longest_query_is_float_or_none(probe: MariaDbStatsProbe) -> None:
    """longest_query_seconds 為 float 或 None（無 active query 時 None）。"""
    data = await probe.sample()
    v = data["longest_query_seconds"]
    assert v is None or isinstance(v, float)
