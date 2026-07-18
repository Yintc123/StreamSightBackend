"""DbStatsProbe 介面 + MariaDb/Pool probe（monitoring.md §2.4）。

ORM 可攜性邊界：
- PoolStatsProbe：讀 SQLAlchemy engine.pool（純 Python，任何後端，零 raw SQL）
- MariaDbStatsProbe：讀 SHOW GLOBAL STATUS / information_schema（MariaDB 專屬）
  藏在 DbStatsProbe 介面後，依 db_dialect 自動選用。
"""

from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

_logger = logging.getLogger(__name__)


@runtime_checkable
class DbStatsProbe(Protocol):
    """可抽換 DB 狀態 probe 介面（monitoring.md §2.4）。"""

    async def sample(self) -> dict:
        """回 DbSample 的部分欄位 dict；能力不足的欄位 None / 'unsupported'。"""
        ...


class PoolStatsProbe:
    """SQLAlchemy engine.pool 指標（無 raw SQL，任何後端）。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @staticmethod
    def _pool_stats(pool: object) -> dict:
        """讀 pool 指標；StaticPool（測試）無這些方法 → fallback 0。"""
        return {
            "size": getattr(pool, "size", lambda: 0)(),
            "checked_out": getattr(pool, "checkedout", lambda: 0)(),
            "overflow": getattr(pool, "overflow", lambda: 0)(),
            "checked_in": getattr(pool, "checkedin", lambda: 0)(),
        }

    async def sample(self) -> dict:
        return {
            "ts": int(time.time() * 1000),
            "pool": self._pool_stats(self._engine.pool),
            "connections": {"connected": 0, "running": 0, "idle": 0},
            "db_size_bytes": None,
            "longest_query_seconds": None,
            "backend": "pool_only",
        }


class MariaDbStatsProbe:
    """MariaDB 專屬 probe（SHOW GLOBAL STATUS / information_schema）。

    每次採樣開短命 session（get_session_factory），不占 request-scoped session。
    """

    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        db_name: str,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self._db_name = db_name

    async def sample(self) -> dict:
        pool_stats = PoolStatsProbe._pool_stats(self._engine.pool)

        connections = {"connected": 0, "running": 0, "idle": 0}
        db_size_bytes: int | None = None
        longest_query_seconds: float | None = None

        try:
            async with self._session_factory() as session:
                # Threads
                result = await session.execute(
                    text(
                        "SELECT VARIABLE_NAME, VARIABLE_VALUE "
                        "FROM information_schema.GLOBAL_STATUS "
                        "WHERE VARIABLE_NAME IN "
                        "('THREADS_CONNECTED','THREADS_RUNNING')"
                    )
                )
                status_map = {row[0]: int(row[1]) for row in result}
                connected = status_map.get("THREADS_CONNECTED", 0)
                running = status_map.get("THREADS_RUNNING", 0)
                connections = {
                    "connected": connected,
                    "running": running,
                    "idle": max(0, connected - running),
                }

                # 最長查詢
                result2 = await session.execute(
                    text(
                        "SELECT MAX(TIME) FROM information_schema.PROCESSLIST "
                        "WHERE COMMAND != 'Sleep'"
                    )
                )
                longest = result2.scalar()
                if longest is not None:
                    longest_query_seconds = float(longest)

                # DB 大小
                result3 = await session.execute(
                    text(
                        "SELECT SUM(DATA_LENGTH + INDEX_LENGTH) "
                        "FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = :db"
                    ),
                    {"db": self._db_name},
                )
                size = result3.scalar()
                if size is not None:
                    db_size_bytes = int(size)

        except Exception:
            _logger.warning("MariaDbStatsProbe sample failed", exc_info=True)

        return {
            "ts": int(time.time() * 1000),
            "pool": pool_stats,
            "connections": connections,
            "db_size_bytes": db_size_bytes,
            "longest_query_seconds": longest_query_seconds,
            "backend": "mariadb",
        }
