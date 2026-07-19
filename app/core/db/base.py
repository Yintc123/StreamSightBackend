from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class RawBase(DeclarativeBase):
    """Mapper registry 根——同一 metadata，但不帶任何欄位。

    給不需要標準 id/created_at/updated_at 的例外 table 繼承（例如高頻時序 table）。
    所有 model 都在同一 mapper registry，conftest.py 的 Base.metadata.create_all 仍然
    會建出繼承 RawBase 的 table（Base.metadata is RawBase.metadata）。
    """

    __abstract__ = True


class Base(RawBase):
    """所有標準 table 的基礎類別：int PK + server-side 時間戳。

    - SQLAlchemy 2.x 用 DeclarativeBase (不是舊的 declarative_base() 函式)。
    - 絕大多數 table 繼承 Base：id + created_at + updated_at 自動帶入。
    - 不需要標準時間戳的例外 table（高頻 append-only 資料）→ 繼承 RawBase。
    """

    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
