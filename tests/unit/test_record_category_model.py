"""RecordCategory model：unique(name) + server_default（records-model.md §7.1）。"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.record_category import RecordCategory


async def test_uq_record_categories_name(db_session: AsyncSession) -> None:
    db_session.add(RecordCategory(name="感測器", label="感測器"))
    await db_session.flush()
    db_session.add(RecordCategory(name="感測器", label="dup"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_is_active_and_sort_order_server_defaults(db_session: AsyncSession) -> None:
    cat = RecordCategory(name="系統", label="系統")
    db_session.add(cat)
    await db_session.flush()
    fetched = (
        await db_session.execute(select(RecordCategory).where(RecordCategory.id == cat.id))
    ).scalar_one()
    assert fetched.is_active is True
    assert fetched.sort_order == 0
