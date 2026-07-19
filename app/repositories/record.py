"""records 表存取（讀取路徑 + 寫入，records-model.md §4）。

repo 只吃**已正規化的乾淨參數**（size 夾值、keyword 已跳脫含 %…%、sort 已型別化成 enum、
category 已轉 id）——正規化與驗證在 service（model §2.7-(1)）。repo 純資料層、不碰字串語意。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import InstrumentedAttribute

from app.core.enums import RecordSortField, SortDirection
from app.models.admin import Admin
from app.models.record import Record
from app.models.record_category import RecordCategory

from .base import BaseRepository


@dataclass(frozen=True)
class RecordListRow:
    """list/get 的一列：record 本體 + JOIN 解析出的分類名與建立者 username（免 N+1，§4）。"""

    record: Record
    category_name: str  # JOIN record_categories.name（§2.4）
    created_by_username: str  # JOIN admins.username（恆命中，無 NULL 分支，§2.3）


# 非 category 排序欄 → Record ORM Column（category 特例走 record_categories.name，§2.4）
_SORT_COLUMNS: dict[RecordSortField, InstrumentedAttribute] = {
    RecordSortField.ID: Record.id,
    RecordSortField.TITLE: Record.title,
    RecordSortField.VALUE: Record.value,
    RecordSortField.CREATED_AT: Record.created_at,
}


def _predicate(
    *, category_id: int | None, keyword: str | None, include_deleted: bool
) -> ColumnElement[bool] | None:
    """count 與 list 共用的謂詞建構器（避免條件漂移，§2.7-(2)）。"""
    clauses: list[ColumnElement[bool]] = []
    if not include_deleted:
        clauses.append(Record.deleted_at.is_(None))
    if category_id is not None:
        clauses.append(Record.category_id == category_id)
    if keyword:
        clauses.append(func.lower(Record.title).like(keyword, escape="\\"))
    if not clauses:
        return None
    combined = clauses[0]
    for clause in clauses[1:]:
        combined = combined & clause
    return combined


def _base_row_select() -> Select:
    """list/get 共用的 select：record + JOIN 帶出 category_name / created_by_username（DRY，§4）。"""
    return (
        select(Record, RecordCategory.name, Admin.username)
        .join(RecordCategory, RecordCategory.id == Record.category_id)
        .join(Admin, Admin.principal_id == Record.created_by_principal_id)
    )


def _rows(result_rows: Sequence) -> list[RecordListRow]:
    return [
        RecordListRow(record=row[0], category_name=row[1], created_by_username=row[2])
        for row in result_rows
    ]


class RecordRepository(BaseRepository[Record]):
    """records 表存取。`get`（PK lookup）繼承自 BaseRepository。"""

    model: type[Record] = Record

    async def list_records(
        self,
        *,
        category_id: int | None,
        keyword: str | None,
        sort_field: RecordSortField,
        sort_dir: SortDirection,
        include_deleted: bool,
        limit: int,
        offset: int,
    ) -> Sequence[RecordListRow]:
        """列表（收已驗證 enum，翻 Column/JOIN）；ORDER BY <col> <dir>, id <dir> 穩定分頁（§2.7）。"""
        stmt = _base_row_select()
        predicate = _predicate(
            category_id=category_id, keyword=keyword, include_deleted=include_deleted
        )
        if predicate is not None:
            stmt = stmt.where(predicate)

        if sort_field is RecordSortField.CATEGORY:
            sort_col: InstrumentedAttribute = (
                RecordCategory.name
            )  # 依分類名，非 category_id（§2.4）
        else:
            sort_col = _SORT_COLUMNS[sort_field]
        direction = (lambda c: c.desc()) if sort_dir is SortDirection.DESC else (lambda c: c.asc())
        stmt = stmt.order_by(direction(sort_col), direction(Record.id)).limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return _rows(result.all())

    async def count_records(
        self, *, category_id: int | None, keyword: str | None, include_deleted: bool
    ) -> int:
        """與 list_records 共用謂詞（篩選後、分頁前筆數，§2.7-(3)）。"""
        stmt = select(func.count()).select_from(Record)
        predicate = _predicate(
            category_id=category_id, keyword=keyword, include_deleted=include_deleted
        )
        if predicate is not None:
            stmt = stmt.where(predicate)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_active(self, record_id: int) -> Record | None:
        """WHERE id=? AND deleted_at IS NULL（供 update/delete 前置；裸列）。"""
        stmt = select(Record).where(Record.id == record_id, Record.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_row(self, record_id: int) -> RecordListRow | None:
        """同 get_active 謂詞 + list 同套 JOIN 解析（供 get/create/update 統一回應，§4）。"""
        stmt = _base_row_select().where(Record.id == record_id, Record.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return RecordListRow(record=row[0], category_name=row[1], created_by_username=row[2])

    async def bulk_insert(self, records: list[Record]) -> None:
        """批次 INSERT（companion service 逐列驗證後呼叫；上限由 service 把關，§4）。"""
        self.session.add_all(records)
        await self.session.flush()
