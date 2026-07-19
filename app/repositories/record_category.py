"""record_categories 表存取（下拉來源 + 寫入/篩選解析，records-model.md §4）。"""

from collections.abc import Sequence

from sqlalchemy import select

from app.models.record_category import RecordCategory

from .base import BaseRepository


class RecordCategoryRepository(BaseRepository[RecordCategory]):
    """record_categories 表存取。`get`（PK lookup）繼承自 BaseRepository。"""

    model: type[RecordCategory] = RecordCategory

    async def list_active(self, *, order_by_sort: bool = True) -> Sequence[RecordCategory]:
        """啟用中的分類（供 GET /records/categories 下拉），ORDER BY sort_order, name。"""
        stmt = select(RecordCategory).where(RecordCategory.is_active.is_(True))
        if order_by_sort:
            stmt = stmt.order_by(RecordCategory.sort_order, RecordCategory.name)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_all(self) -> Sequence[RecordCategory]:  # type: ignore[override]
        """不濾 is_active（供後台分類管理，若 companion 提供）。"""
        stmt = select(RecordCategory).order_by(RecordCategory.sort_order, RecordCategory.name)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_name(self, name: str) -> RecordCategory | None:
        """name → 分類列（純存在查找、不判 active）。呼叫端決定 active 語意（§4）。"""
        stmt = select(RecordCategory).where(RecordCategory.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
