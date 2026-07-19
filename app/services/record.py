"""RecordService — 輸入正規化 + 業務驗證 + 交易邊界（records-service.md §3）。

授權（grade 階梯）在 router（`require_min_admin_role`），本層信任傳入的 actor。commit 在 service、
repo 只 flush/add（比照 AdminService）。統一解析路徑：write 後一律 `get_active_row` 回 RecordListRow。
"""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from datetime import date as DateType

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_app_settings
from app.core.enums import DEFAULT_SORT, RecordSortField, SortDirection
from app.core.exceptions import RecordNotFoundError, RecordValidationError
from app.dtos.record import ImportResult, RecordCreate, RecordUpdate, RowError
from app.models.admin import Admin
from app.models.record import Record
from app.models.record_category import RecordCategory
from app.repositories.record import RecordListRow, RecordRepository
from app.repositories.record_category import RecordCategoryRepository

_BULK_MAX_ROWS = 1000


class RecordService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = RecordRepository(session)
        self.category_repo = RecordCategoryRepository(session)

    # ── 讀取 ──────────────────────────────────────────────────────
    async def list_records(
        self,
        *,
        page: int,
        size: int,
        category: str | None,
        keyword: str | None,
        date_from: DateType | None = None,
        date_to: DateType | None = None,
        sort: str,
        include_deleted: bool,
    ) -> tuple[Sequence[RecordListRow], int, int, int]:
        """列表：正規化（夾值/sort→enum/keyword 跳脫/category 名→id/日期→UTC datetime）→ 委派 repo（§3.1）。

        兩層 page size（§2.7-(1)）：有日期範圍 → analytics_max（5000）；否則 list_max（100）。
        date_to 推進至隔日 00:00 UTC（開區間右端，含當天末）。
        """
        cfg = get_app_settings()
        has_date_range = date_from is not None or date_to is not None
        max_size = (
            cfg.records_analytics_max_page_size
            if has_date_range
            else cfg.records_list_max_page_size
        )
        size = min(max(size, 1), max_size)
        page = max(page, 1)

        sort_field, sort_dir = self._parse_sort(sort)
        escaped_kw = self._escape_like(keyword)
        category_id = await self._resolve_filter_category(category)
        dt_from = self._date_to_utc_start(date_from)
        dt_to = self._date_to_utc_exclusive_end(date_to)

        offset = (page - 1) * size
        rows = await self.repo.list_records(
            category_id=category_id,
            keyword=escaped_kw,
            date_from=dt_from,
            date_to=dt_to,
            sort_field=sort_field,
            sort_dir=sort_dir,
            include_deleted=include_deleted,
            limit=size,
            offset=offset,
        )
        total = await self.repo.count_records(
            category_id=category_id,
            keyword=escaped_kw,
            date_from=dt_from,
            date_to=dt_to,
            include_deleted=include_deleted,
        )
        return rows, total, page, size

    async def get_record(self, record_id: int) -> RecordListRow:
        row = await self.repo.get_active_row(record_id)
        if row is None:
            raise RecordNotFoundError(f"Record {record_id} not found")
        return row

    async def list_categories(self) -> Sequence[RecordCategory]:
        return await self.category_repo.list_active(order_by_sort=True)

    # ── 寫入 ──────────────────────────────────────────────────────
    async def create_record(self, payload: RecordCreate, actor: Admin) -> RecordListRow:
        category_id = await self._resolve_writable_category(payload.category)
        title = self._clean_title(payload.title)
        try:
            record = Record(
                title=title,
                value=payload.value,
                category_id=category_id,
                created_by_principal_id=actor.principal_id,
                note=payload.note,
            )
            await self.repo.add(record)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return await self._row_or_raise(record.id)

    async def update_record(
        self, record_id: int, payload: RecordUpdate, actor: Admin
    ) -> RecordListRow:
        record = await self.repo.get_active(record_id)
        if record is None:
            raise RecordNotFoundError(f"Record {record_id} not found")
        category_id = await self._resolve_writable_category(payload.category)
        title = self._clean_title(payload.title)
        try:
            record.title = (
                title  # 只改四欄；created_by/created_at 不動、updated_at 由 onupdate 刷新
            )
            record.value = payload.value
            record.category_id = category_id
            record.note = payload.note
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return await self._row_or_raise(record_id)

    async def delete_record(self, record_id: int, actor: Admin) -> None:
        record = await self.repo.get_active(record_id)
        if record is None:
            raise RecordNotFoundError(f"Record {record_id} not found")
        try:
            record.deleted_at = datetime.now(UTC)  # 軟刪除（§2.2）
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def bulk_create(self, rows: list[dict], actor: Admin) -> ImportResult:
        if len(rows) > _BULK_MAX_ROWS:
            raise RecordValidationError(f"too many rows (max {_BULK_MAX_ROWS})")
        valid: list[Record] = []
        errors: list[RowError] = []
        for i, row in enumerate(rows):
            result = await self._validate_row(row, actor)
            if isinstance(result, str):
                errors.append(RowError(row_index=i, reason=result))
            else:
                valid.append(result)
        try:
            await self.repo.bulk_insert(valid)  # 整批一交易（§3.6）
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return ImportResult(created=len(valid), errors=errors)

    # ── 私有輔助（正規化與解析，§3.7）────────────────────────────────
    @staticmethod
    def _date_to_utc_start(d: DateType | None) -> datetime | None:
        """date → 當日 00:00:00 UTC（date_from 下界，含）。"""
        if d is None:
            return None
        return datetime(d.year, d.month, d.day, tzinfo=UTC)

    @staticmethod
    def _date_to_utc_exclusive_end(d: DateType | None) -> datetime | None:
        """date → 隔日 00:00:00 UTC（date_to 上界，開區間，含當天末）。"""
        if d is None:
            return None
        next_day = d + timedelta(days=1)
        return datetime(next_day.year, next_day.month, next_day.day, tzinfo=UTC)

    def _parse_sort(self, sort: str) -> tuple[RecordSortField, SortDirection]:
        """`"field:dir"` → enum；非法欄名/方向/格式 → RecordValidationError（唯一驗證點）。"""
        parts = (sort or DEFAULT_SORT).split(":")
        if len(parts) != 2:
            raise RecordValidationError(f"invalid sort: {sort!r}")
        field_str, dir_str = parts
        try:
            return RecordSortField(field_str), SortDirection(dir_str)
        except ValueError as e:
            raise RecordValidationError(f"invalid sort: {sort!r}") from e

    def _escape_like(self, keyword: str | None) -> str | None:
        """None/空/純空白 → None；否則跳脫 %/_/\\ 並包 %…%（配 repo ESCAPE '\\'）。"""
        if keyword is None:
            return None
        kw = keyword.strip().lower()
        if not kw:
            return None
        kw = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{kw}%"

    async def _resolve_filter_category(self, name: str | None) -> int | None:
        """篩選路徑：允許 inactive（退場分類舊資料仍可篩）；名不存在 → 422（§2.7-(1)）。"""
        if name is None or name == "":
            return None
        cat = await self.category_repo.get_by_name(name)
        if cat is None:
            raise RecordValidationError(f"category not found: {name}")
        return cat.id

    async def _resolve_writable_category(self, name: str) -> int:
        """寫入路徑：名不存在或 is_active=False → 422（不得用退場分類建資料，§2.4）。"""
        cat = await self.category_repo.get_by_name(name)
        if cat is None or not cat.is_active:
            raise RecordValidationError(f"category not found or inactive: {name}")
        return cat.id

    def _clean_title(self, title: str) -> str:
        stripped = title.strip()
        if not stripped:
            raise RecordValidationError("title must not be empty")
        return stripped

    async def _validate_row(self, row: dict, actor: Admin) -> Record | str:
        """匯入單列驗證：合法回 Record、非法回錯誤原因字串（fail-closed，§3.7）。"""
        title = row.get("title")
        if not isinstance(title, str) or not title.strip():
            return "title must be a non-empty string"
        raw_value = row.get("value")
        if raw_value is None:
            return "value is required"
        try:
            value = float(raw_value)  # 接受 int/float/str（CSV 產物）
        except (TypeError, ValueError):
            return f"value is not a number: {raw_value!r}"
        if math.isnan(value) or math.isinf(value):
            return "value must be finite"
        category = row.get("category")
        if not isinstance(category, str):
            return "category must be a string"
        cat = await self.category_repo.get_by_name(category)
        if cat is None or not cat.is_active:
            return f"category not found or inactive: {category}"
        return Record(
            title=title.strip(),
            value=value,
            category_id=cat.id,
            created_by_principal_id=actor.principal_id,
            note=str(row.get("note", "")),
        )

    async def _row_or_raise(self, record_id: int) -> RecordListRow:
        row = await self.repo.get_active_row(record_id)
        if row is None:  # 理論上 commit 後必命中；防禦性
            raise RecordNotFoundError(f"Record {record_id} not found")
        return row
