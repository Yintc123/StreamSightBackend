from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.

    - SQLAlchemy 2.x 用 DeclarativeBase (不是舊的 declarative_base() 函式)。
    - 所有 model 都 `class X(Base): ...` 從這繼承。
    - 共通欄位：
        - `id`         int autoincrement PK
        - `created_at` 建立時間（server-side default）
        - `updated_at` 最後更新時間（每次 UPDATE 自動刷新）

    絕大多數 table 都需要合成 int PK + 時間戳：
      - 內部 join 快
      - Rename 業務欄位不會 cascade FK 災難
      - 時間戳統一格式（帶 timezone、DB 負責產生）

    對於**極少數**不需要 int PK 的例外（純 junction table 的複合 PK、
    UUID audit log），**不要繼承 `Base`**，另外自行宣告即可：

        class UserRole(DeclarativeBase):
            __tablename__ = "user_roles"
            user_id: Mapped[int] = mapped_column(primary_key=True)
            role_id: Mapped[int] = mapped_column(primary_key=True)
    """
    __abstract__ = True  # Base 本身不對應 table

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
