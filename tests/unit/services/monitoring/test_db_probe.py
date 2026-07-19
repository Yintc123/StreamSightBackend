"""DbStatsProbe：PoolStatsProbe / 假 probe + DbStatsService（monitoring.md §2.4/§7.4）。"""

from app.dtos.dto_monitoring import DbSample
from app.services.monitoring.db_probe import DbStatsProbe, PoolStatsProbe
from app.services.monitoring.db_stats import DbStatsService


class FakeProbe:
    """注入假 probe 供 Service 測試（monitoring.md §7.0 設計原則）。"""

    def __init__(self, data: dict) -> None:
        self._data = data

    async def sample(self) -> dict:
        return self._data


def test_pool_probe_satisfies_protocol(engine) -> None:
    """PoolStatsProbe 滿足 DbStatsProbe Protocol（structural subtyping）。"""
    probe = PoolStatsProbe(engine)
    assert isinstance(probe, DbStatsProbe)


async def test_pool_probe_returns_pool_stats(engine) -> None:
    probe = PoolStatsProbe(engine)
    data = await probe.sample()
    assert "pool" in data
    assert "size" in data["pool"]
    assert "checked_out" in data["pool"]
    assert data["backend"] == "pool_only"
    assert data["db_size_bytes"] is None


async def test_db_stats_service_snapshot(engine) -> None:
    probe = FakeProbe(
        {
            "ts": 1000,
            "pool": {"size": 5, "checked_out": 1, "overflow": 0, "checked_in": 4},
            "connections": {"connected": 3, "running": 1, "idle": 2},
            "db_size_bytes": 1024,
            "longest_query_seconds": 0.5,
            "backend": "mariadb",
        }
    )
    svc = DbStatsService(probe)
    snap = await svc.snapshot()

    assert isinstance(snap, DbSample)
    assert snap.backend == "mariadb"
    assert snap.db_size_bytes == 1024
    assert snap.longest_query_seconds == 0.5


async def test_db_stats_service_snapshot_nulls(engine) -> None:
    probe = FakeProbe(
        {
            "ts": 999,
            "pool": {"size": 1, "checked_out": 0, "overflow": 0, "checked_in": 1},
            "connections": {"connected": 0, "running": 0, "idle": 0},
            "backend": "pool_only",
        }
    )
    svc = DbStatsService(probe)
    snap = await svc.snapshot()
    assert snap.db_size_bytes is None
    assert snap.longest_query_seconds is None
