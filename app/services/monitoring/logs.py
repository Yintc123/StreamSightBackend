"""LogQueryService：從 Store 查詢日誌，app 端篩選（monitoring.md §2.7）。"""

from __future__ import annotations

from app.dtos.monitoring import LogEntry, Page
from app.services.monitoring.store import TimeSeriesStore

_LOG_STREAM = "monitor:stream:logs"


class LogQueryService:
    def __init__(self, store: TimeSeriesStore) -> None:
        self._store = store

    async def query(
        self,
        *,
        level: str | None = None,
        since: int | None = None,
        until: int | None = None,
        request_id: str | None = None,
        logger: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[LogEntry]:
        # 向 Store 多取一些以應對篩選後的縮減，但限制掃描窗不過大
        fetch_limit = min(limit * 4, 2000)
        raw_page = await self._store.query(
            _LOG_STREAM, since=since, until=until, cursor=cursor, limit=fetch_limit
        )

        # app 端篩選：收集 ≤ limit 筆，記錄最後採納的 raw _id
        items: list[LogEntry] = []
        last_accepted_id: str | None = None
        raw_exhausted = True

        for raw_item in raw_page.items:
            if level and raw_item.get("level") != level:
                continue
            if request_id and raw_item.get("request_id") != request_id:
                continue
            if logger and raw_item.get("logger") != logger:
                continue
            items.append(
                LogEntry(
                    ts=int(raw_item.get("ts", 0)),
                    level=raw_item.get("level", ""),
                    logger=raw_item.get("logger", ""),
                    message=raw_item.get("message", ""),
                    request_id=raw_item.get("request_id") or None,
                    module=raw_item.get("module") or None,
                    func=raw_item.get("func") or None,
                    line=int(raw_item["line"]) if raw_item.get("line") else None,
                )
            )
            last_accepted_id = raw_item["_id"]
            if len(items) >= limit:
                raw_exhausted = False
                break

        # 下頁游標：達到 limit（可能還有更多）→ 用最後接受筆的 _id；
        # raw 已讀完但 store 還有更多 → 傳遞 store 的游標。
        if not raw_exhausted:
            next_cursor = last_accepted_id
        elif raw_page.next_cursor is not None:
            next_cursor = raw_page.next_cursor
        else:
            next_cursor = None

        return Page(items=items, next_cursor=next_cursor)
