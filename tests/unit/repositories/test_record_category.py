"""RecordCategoryRepository：list_active / get_by_name（records-model.md §7.3）。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.record_category import RecordCategory
from app.repositories.record_category import RecordCategoryRepository


async def test_list_active_excludes_inactive_and_orders(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            RecordCategory(name="b-cat", label="B", sort_order=1, is_active=True),
            RecordCategory(name="a-cat", label="A", sort_order=1, is_active=True),
            RecordCategory(name="z-off", label="Z", sort_order=0, is_active=False),
        ]
    )
    await db_session.flush()
    repo = RecordCategoryRepository(db_session)

    active = await repo.list_active(order_by_sort=True)
    names = [c.name for c in active]
    assert "z-off" not in names  # inactive 排除
    assert names == ["a-cat", "b-cat"]  # ORDER BY sort_order, name（同 sort_order 依 name）


async def test_get_by_name_hit_and_miss(db_session: AsyncSession) -> None:
    db_session.add(RecordCategory(name="感測器", label="感測器", is_active=False))
    await db_session.flush()
    repo = RecordCategoryRepository(db_session)

    hit = await repo.get_by_name("感測器")
    assert hit is not None
    assert hit.is_active is False  # 純存在查找、不判 active（呼叫端決定語意）
    assert await repo.get_by_name("不存在") is None
