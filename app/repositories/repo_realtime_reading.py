"""RealtimeReadingRepository — 批次寫入與範圍查詢（realtime-history.md §5.1）。

不繼承 BaseRepository：RealtimeReading 繼承 RawBase（無標準 id/timestamps），
且介面（bulk_insert + list）與通用 CRUD 模式無關。
commit 由呼叫者（streamer._flush / service）負責；bulk_insert 是例外，因 streamer
持有 sessionmaker 而非 session，直接 commit 以關閉 session。
"""

from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.realtime_reading import RealtimeReading


class RealtimeReadingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert(self, rows: list[dict]) -> None:
        """批次 INSERT（rows: [{"value": float, "ts": datetime(naive UTC)}]）。

        空 list 直接返回，不發 SQL。
        """
        if not rows:
            return
        await self._session.execute(insert(RealtimeReading), rows)

    async def list(
        self,
        from_dt: datetime,
        to_dt: datetime,
        size: int = 1000,
    ) -> list[RealtimeReading]:
        """WHERE ts >= from_dt AND ts < to_dt ORDER BY ts ASC LIMIT size（§5.1）。"""
        result = await self._session.execute(
            select(RealtimeReading)
            .where(RealtimeReading.ts >= from_dt)
            .where(RealtimeReading.ts < to_dt)
            .order_by(RealtimeReading.ts.asc())
            .limit(size)
        )
        return list(result.scalars().all())
