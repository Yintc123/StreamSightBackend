"""RealtimeReading ORM model（realtime-history.md §4.2）。

繼承 RawBase（非 Base）：高頻 append-only 資料不需要 created_at/updated_at。
ts 存 naive UTC（DATETIME(6)，無 timezone 資訊）；讀出後 API 層加 +00:00。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import RawBase


class RealtimeReading(RawBase):
    __tablename__ = "realtime_readings"

    # Integer（SQLite autoincrement 相容）；MySQL/MariaDB 以 migration 建 BIGINT UNSIGNED（§4.1）
    id: Mapped[int] = mapped_column(
        Integer().with_variant(BigInteger, "mysql"),
        primary_key=True,
        autoincrement=True,
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, index=True
    )  # naive UTC；index 供範圍查詢（§4.1）
