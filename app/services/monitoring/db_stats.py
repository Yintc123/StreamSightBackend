"""DbStatsService：DB 狀態快照（monitoring.md §4）。"""

from __future__ import annotations

from app.dtos.monitoring import DbSample
from app.services.monitoring.db_probe import DbStatsProbe


class DbStatsService:
    def __init__(self, probe: DbStatsProbe) -> None:
        self._probe = probe

    async def snapshot(self) -> DbSample:
        data = await self._probe.sample()
        return DbSample(
            ts=data["ts"],
            pool=data["pool"],
            connections=data["connections"],
            db_size_bytes=data.get("db_size_bytes"),
            longest_query_seconds=data.get("longest_query_seconds"),
            backend=data.get("backend", "unknown"),
        )
