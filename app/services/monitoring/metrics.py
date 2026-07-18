"""MetricQueryService：歷史 range 查詢（monitoring.md §2.7/§4）。"""

from __future__ import annotations

from app.dtos.monitoring import Page
from app.services.monitoring.store import TimeSeriesStore

_ALLOWED_STREAMS = {"db"}


class MetricQueryService:
    def __init__(self, store: TimeSeriesStore) -> None:
        self._store = store

    async def range(
        self,
        name: str,
        *,
        since: int | None = None,
        until: int | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[dict]:
        if name not in _ALLOWED_STREAMS:
            return Page(items=[], next_cursor=None)
        stream = f"monitor:stream:{name}"
        return await self._store.query(stream, since=since, until=until, cursor=cursor, limit=limit)
