"""RealtimeHistoryService — 歷史資料查詢 thin wrapper（realtime-history.md §3.2）。

分層職責：API 層只做輸入驗證；商業邏輯（from < to 檢查）在此；DB 存取在 repo。
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.realtime_reading import RealtimeReading
from app.repositories.realtime_reading import RealtimeReadingRepository


class RealtimeHistoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = RealtimeReadingRepository(session)

    async def list_history(
        self,
        from_dt: datetime,
        to_dt: datetime,
        size: int,
    ) -> list[RealtimeReading]:
        if from_dt >= to_dt:
            raise BadRequestError("'from' must be before 'to'")
        return await self._repo.list(from_dt, to_dt, size)
