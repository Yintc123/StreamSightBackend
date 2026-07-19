"""TimeSeriesStore 介面 + RedisStreamStore（monitoring.md §2.2）。

interim：Redis Stream（XADD MAXLEN ~ / XRANGE）
future：CloudWatchLogStore / ELKLogStore / InfluxStore —— 同介面換 adapter，上層零改。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from redis.asyncio import Redis

from app.dtos.monitoring import Page

if TYPE_CHECKING:
    pass


@runtime_checkable
class TimeSeriesStore(Protocol):
    """可抽換時序 Store 介面（monitoring.md §2.2）。"""

    async def append(
        self, stream: str, entry: dict, *, maxlen: int | None = None, minid: int | None = None
    ) -> str:
        """寫入一筆，回 entry id（<ms>-<seq>）。"""
        ...

    async def append_many(
        self,
        stream: str,
        entries: list[dict],
        *,
        maxlen: int | None = None,
        minid: int | None = None,
    ) -> list[str]:
        """批次寫入 N 筆（pipeline transaction=False，N 筆 → 1 round-trip），回 id list。"""
        ...

    async def query(
        self,
        stream: str,
        *,
        since: int | None = None,
        until: int | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[dict]:
        """時間/ID 區間 + 游標分頁查詢，回 Page[dict]（每項含 _id 欄位）。"""
        ...


class RedisStreamStore:
    """Redis Stream 實作（XADD MAXLEN ~ / XRANGE）。"""

    def __init__(self, client: Redis) -> None:
        self._r = client

    async def append(
        self, stream: str, entry: dict, *, maxlen: int | None = None, minid: int | None = None
    ) -> str:
        raw_id = await self._r.xadd(
            stream,
            {k: str(v) for k, v in entry.items()},
            maxlen=maxlen,
            approximate=maxlen is not None or minid is not None,
            minid=minid,
        )
        return raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)

    async def append_many(
        self,
        stream: str,
        entries: list[dict],
        *,
        maxlen: int | None = None,
        minid: int | None = None,
    ) -> list[str]:
        """批次寫入 N 筆（pipeline transaction=False，N 筆 → 1 round-trip），回 id list。"""
        if not entries:
            return []
        approximate = maxlen is not None or minid is not None
        pipe = self._r.pipeline(transaction=False)
        for entry in entries:
            pipe.xadd(
                stream,
                {k: str(v) for k, v in entry.items()},
                maxlen=maxlen,
                approximate=approximate,
                minid=minid,
            )
        raw_ids = await pipe.execute()
        return [raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id) for raw_id in raw_ids]

    async def query(
        self,
        stream: str,
        *,
        since: int | None = None,
        until: int | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[dict]:
        # 起點：cursor 取下一筆（exclusive）；否則用 since 轉 ID；預設 "-"（最舊）
        if cursor is not None:
            start = f"({cursor}"
        elif since is not None:
            start = f"{since}-0"
        else:
            start = "-"

        # 終點：until 轉 ID；預設 "+"（最新）
        end = f"{until}-9999999" if until is not None else "+"

        raw = await self._r.xrange(stream, start, end, count=limit + 1) or []
        has_more = len(raw) > limit
        raw = raw[:limit]

        items: list[dict] = []
        for entry_id, fields in raw:
            if fields is None:
                continue
            eid = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
            item = {
                (k.decode() if isinstance(k, bytes) else k): (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in fields.items()
            }
            item["_id"] = eid
            items.append(item)

        next_cursor = items[-1]["_id"] if has_more and items else None
        return Page(items=items, next_cursor=next_cursor)
