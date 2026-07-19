"""Record model — 資料記錄業務表（軟刪除 + 建立者稽核）。

前端 `Record` dataclass 的持久化後端（records-model.md）。可編輯欄位 title/value/category/note；
建立者 FK 釘 `admins.principal_id`（RESTRICT + NOT NULL）硬化「建立者必為 admin」（§2.1）；
分類走代理鍵 `category_id` FK → `record_categories.id`（§2.4）；軟刪除單欄 `deleted_at`（§2.2）。
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Record(Base):
    __tablename__ = "records"

    # 標題（必填非空、明文可子字串搜尋，§2.5）
    title: Mapped[str] = mapped_column(String(200))
    # 量測值（雙精度，對齊前端 value: float，§2.6）
    value: Mapped[float] = mapped_column(Float)
    # 分類（代理鍵 FK → record_categories.id；API 邊界解析回 category 字串，§2.4）
    # 顯式 index：有 reader（分類篩選/join），§3.3。
    category_id: Mapped[int] = mapped_column(Integer, index=True)
    # 建立者（不可變、恆為 admin、稽核/顯示用、不決定授權，§2.1/§2.3/§2.9）。
    # **不顯式建 index**（§3.3：無 reader、純寫入放大）；對齊 admins.archived_by/deleted_by 慣例。
    # FK 於 InnoDB 仍會自動索引（滿足 FK 需求）；解析走 JOIN admins（admin 側 principal_id 已 unique index）。
    created_by_principal_id: Mapped[int] = mapped_column(Integer)
    # 備註（可選，前端 note=""）
    note: Mapped[str] = mapped_column(String(500), default="", server_default=text("''"))
    # 軟刪除時間（NULL＝未刪除；終態，§2.2）
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    __table_args__ = (
        # 建立者必為 admin（DB 硬化）；RESTRICT + NOT NULL（不可變、恆存，§2.1/§3.2）
        ForeignKeyConstraint(
            ["created_by_principal_id"],
            ["admins.principal_id"],
            ondelete="RESTRICT",
            name="fk_records_creator_admin",
        ),
        # 分類代理鍵 FK；被參照分類不可硬刪，退場走 is_active=False（§2.4）
        ForeignKeyConstraint(
            ["category_id"],
            ["record_categories.id"],
            ondelete="RESTRICT",
            name="fk_records_category",
        ),
        # title 非空（DB 兜底；service 亦驗，§2.5）。用 length() 而非 char_length()：
        # 前者於 SQLite（測試 create_all）與 MariaDB 皆為內建，非空檢查語意等價、可攜。
        CheckConstraint("length(title) > 0", name="ck_records_title_nonempty"),
    )

    @property
    def is_active(self) -> bool:
        """未軟刪除視為 active（列表預設可見；§3.4）。"""
        return self.deleted_at is None

    def __repr__(self) -> str:
        return f"<Record id={self.id} title={self.title!r}>"
