"""HTTP response / request schemas for records endpoints（records-api.md §4）。

回應 DTO 以 `from_row` 承接 repo JOIN 解析（比照 AdminSummary.from_row）。請求 body（create/update）
直接用 `app/dtos/record.py` 的 RecordCreate/RecordUpdate（不另立 router request schema，§4.2）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.models.record_category import RecordCategory
    from app.repositories.record import RecordListRow


class RecordSummary(BaseModel):
    """對齊前端 Record dataclass（逐欄，data-source.md §資料契約）。無 updated_by（§4.1）。"""

    id: int
    title: str
    value: float
    category: str  # 解析自 category_id → record_categories.name（§2.4）
    created_by: str  # 解析自 created_by_principal_id → admins.username（§2.3）
    created_at: datetime
    updated_at: datetime
    note: str
    deleted_at: datetime | None  # include_deleted 時可為非 None

    @classmethod
    def from_row(cls, row: RecordListRow) -> RecordSummary:
        r = row.record
        return cls(
            id=r.id,
            title=r.title,
            value=r.value,
            category=row.category_name,
            created_by=row.created_by_username,
            created_at=r.created_at,
            updated_at=r.updated_at,
            note=r.note,
            deleted_at=r.deleted_at,
        )


class RecordPage(BaseModel):
    """對齊前端 Page dataclass：{items,total,page,size}（1-based page，§4.3）。"""

    items: list[RecordSummary]
    total: int  # 篩選後、分頁前筆數
    page: int  # 1-based（回傳夾值後的實際頁碼）
    size: int  # 回傳夾值後的實際每頁筆數


class Category(BaseModel):
    """分類下拉來源（§4.5）。"""

    name: str  # 分類值（前端 CATEGORIES 字串）
    label: str  # 下拉顯示文字
    sort_order: int

    @classmethod
    def from_model(cls, category: RecordCategory) -> Category:
        return cls(name=category.name, label=category.label, sort_order=category.sort_order)


class BulkCreateRequest(BaseModel):
    """匯入請求：rows 寬鬆（逐列驗證於 service），max 1000（整批 422，§4.4）。"""

    rows: list[dict[str, Any]] = Field(max_length=1000)
