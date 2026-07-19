"""RecordCategory model — records 分類查詢表（下拉選單的權威來源）。

`records.category_id` 之 FK 目標（代理鍵）。分類值（name）＝前端 CATEGORIES 字串；
改名/停用只動本表一列、不觸及 records（rename-safe，records-model.md §2.4/§3.6）。
"""

from sqlalchemy import Boolean, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RecordCategory(Base):
    __tablename__ = "record_categories"

    # 分類值＝前端 CATEGORIES 字串（如 "感測器"）；unique，供 API name↔id 雙向解析（§2.4）
    name: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    # 下拉顯示文字（種子時 = name；預留 i18n/改顯示名）
    label: Mapped[str] = mapped_column(String(50))
    # 下拉排序（小→大）
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # 啟用中才進下拉；停用＝退場但保留列（既有 records FK 仍有效，§2.4）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))

    __table_args__ = (UniqueConstraint("name", name="uq_record_categories_name"),)

    def __repr__(self) -> str:
        return f"<RecordCategory id={self.id} name={self.name!r}>"
