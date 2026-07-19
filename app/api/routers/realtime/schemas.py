"""Response schemas for /realtime endpoints（realtime-history.md §5.3）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.models.realtime_reading import RealtimeReading


class ReadingItem(BaseModel):
    """單筆讀值（realtime-history.md §5.3 response schema）。"""

    model_config = ConfigDict(from_attributes=True)

    value: float
    ts: datetime


class HistoryPage(BaseModel):
    """歷史查詢頁（§5.3）。

    `from_` 在 JSON 序列化為 `"from"`（alias）；
    `populate_by_name=True` 允許 Python 端用 `from_=` 建構。
    FastAPI 的 jsonable_encoder 預設 by_alias=True，alias 自動生效。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[ReadingItem]
    from_: datetime = Field(alias="from")
    to: datetime

    @classmethod
    def from_query(
        cls,
        items: list[RealtimeReading],
        from_dt: datetime,
        to_dt: datetime,
    ) -> HistoryPage:
        return cls(
            items=[ReadingItem(value=r.value, ts=r.ts.replace(tzinfo=UTC)) for r in items],
            **{"from": from_dt, "to": to_dt},
        )
