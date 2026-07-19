"""RecordService：正規化 + CRUD + 匯入（records-service.md §6）。"""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole
from app.core.exceptions import RecordNotFoundError, RecordValidationError
from app.dtos.record import RecordCreate, RecordUpdate
from app.models.admin import Admin
from app.models.record import Record
from app.models.record_category import RecordCategory
from app.services import AdminService, RecordService


async def _actor(db_session: AsyncSession, suffix: str = "act") -> Admin:
    return await AdminService(db_session).create(
        username=f"rec-{suffix}", name="A", password="longpassword", admin_role=AdminRole.EDITOR
    )


def _svc(db_session: AsyncSession) -> RecordService:
    return RecordService(db_session)


def _list_kwargs(**over) -> dict:
    base = {
        "page": 1,
        "size": 20,
        "category": None,
        "keyword": None,
        "sort": "id:asc",
        "include_deleted": False,
    }
    base.update(over)
    return base


# ── 6.1 正規化 ─────────────────────────────────────────────────
async def test_size_page_clamp(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    _, _, page, size = await svc.list_records(**_list_kwargs(size=10**6, page=1))
    assert size == 100  # 夾至 records_max_page_size
    _, _, page0, size0 = await svc.list_records(**_list_kwargs(size=0, page=0))
    assert size0 == 1 and page0 == 1


async def test_parse_sort_valid_and_invalid(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    # 合法 → 不拋
    await svc.list_records(**_list_kwargs(sort="title:desc"))
    # 空字串套 DEFAULT_SORT
    await svc.list_records(**_list_kwargs(sort=""))
    # 非法欄名 / 方向 / 格式 → 422
    for bad in ["bogus:asc", "id:sideways", "idasc"]:
        with pytest.raises(RecordValidationError):
            await svc.list_records(**_list_kwargs(sort=bad))


async def test_filter_category_inactive_allowed_and_missing_422(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    off = RecordCategory(name="退場", label="退場", is_active=False)
    db_session.add(off)
    await db_session.flush()
    # 篩選允許 inactive（不拋）
    await svc.list_records(**_list_kwargs(category="退場"))
    # 名不存在 → 422
    with pytest.raises(RecordValidationError):
        await svc.list_records(**_list_kwargs(category="不存在"))


# ── 6.2 CRUD ───────────────────────────────────────────────────
async def test_create_record_persists_and_resolves(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    actor = await _actor(db_session)
    row = await svc.create_record(
        RecordCreate(title="Hello", value=3.5, category="感測器", note="n"), actor
    )
    assert row.record.created_by_principal_id == actor.principal_id
    assert row.created_by_username == "rec-act"
    assert row.category_name == "感測器"
    stored = (
        await db_session.execute(select(Record).where(Record.id == row.record.id))
    ).scalar_one()
    assert stored.title == "Hello"


async def test_create_record_inactive_category_422(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    actor = await _actor(db_session, "ci")
    off = RecordCategory(name="退場", label="退場", is_active=False)
    db_session.add(off)
    await db_session.flush()
    with pytest.raises(RecordValidationError):
        await svc.create_record(RecordCreate(title="X", value=1.0, category="退場"), actor)


async def test_get_update_delete_lifecycle(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    creator = await _actor(db_session, "creator")
    editor = await _actor(db_session, "editor")
    created = await svc.create_record(
        RecordCreate(title="Orig", value=1.0, category="感測器"), creator
    )
    rid = created.record.id

    # get 命中
    got = await svc.get_record(rid)
    assert got.record.title == "Orig"

    # update：只改四欄、created_by 為原建立者（非 actor）
    updated = await svc.update_record(
        rid, RecordUpdate(title="Changed", value=2.0, category="系統", note="u"), editor
    )
    assert updated.record.title == "Changed"
    assert updated.record.value == 2.0
    assert updated.category_name == "系統"
    assert updated.created_by_username == "rec-creator"  # 原建立者、非 editor

    # delete：軟刪除、再 get → 404
    await svc.delete_record(rid, editor)
    with pytest.raises(RecordNotFoundError):
        await svc.get_record(rid)


async def test_get_missing_raises(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    with pytest.raises(RecordNotFoundError):
        await _svc(db_session).get_record(99999)


# ── 6.3 匯入 ───────────────────────────────────────────────────
async def test_bulk_all_valid(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    actor = await _actor(db_session, "bulk1")
    rows = [
        {"title": "A", "value": 1.0, "category": "感測器"},
        {"title": "B", "value": "2.5", "category": "系統"},  # value 字串亦接受
    ]
    result = await svc.bulk_create(rows, actor)
    assert result.created == 2
    assert result.errors == []
    total = (await db_session.execute(select(Record))).scalars().all()
    assert len(total) == 2


async def test_bulk_mixed_collects_errors_without_aborting(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    actor = await _actor(db_session, "bulk2")
    rows = [
        {"title": "ok", "value": 1.0, "category": "感測器"},
        {"title": "", "value": 1.0, "category": "感測器"},  # title 空
        {"title": "bad-val", "value": "abc", "category": "感測器"},  # value 非數
        {"title": "bad-cat", "value": 1.0, "category": "不存在"},  # category 不存在
    ]
    result = await svc.bulk_create(rows, actor)
    assert result.created == 1
    assert [e.row_index for e in result.errors] == [1, 2, 3]  # 0-based
    stored = (await db_session.execute(select(Record))).scalars().all()
    assert len(stored) == 1


async def test_bulk_over_limit_422_zero_persisted(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    actor = await _actor(db_session, "bulk3")
    rows = [{"title": "x", "value": 1.0, "category": "感測器"}] * 1001
    with pytest.raises(RecordValidationError):
        await svc.bulk_create(rows, actor)
    stored = (await db_session.execute(select(Record))).scalars().all()
    assert len(stored) == 0


async def test_list_categories_active_only_sorted(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    svc = _svc(db_session)
    db_session.add(RecordCategory(name="退場", label="退場", is_active=False, sort_order=99))
    await db_session.flush()
    cats = await svc.list_categories()
    names = [c.name for c in cats]
    assert "退場" not in names
    assert names == ["感測器", "系統", "應用", "網路"]  # 依 sort_order


# ── 6.4 日期範圍（§2.7-(1)-(2)）──────────────────────────────────


async def test_date_from_future_excludes_all(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """date_from 設遠未來 → total=0（服務端日期正規化正確傳入 repo）。"""
    svc = _svc(db_session)
    actor = await _actor(db_session, "df1")
    await svc.create_record(RecordCreate(title="X", value=1.0, category="感測器"), actor)
    _, total, _, _ = await svc.list_records(**_list_kwargs(date_from=date(2999, 12, 31)))
    assert total == 0


async def test_date_to_past_excludes_all(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """date_to 設遠過去 → total=0（date_to 推進一天後仍不含今天紀錄）。"""
    svc = _svc(db_session)
    actor = await _actor(db_session, "df2")
    await svc.create_record(RecordCreate(title="Y", value=1.0, category="感測器"), actor)
    _, total, _, _ = await svc.list_records(**_list_kwargs(date_to=date(2000, 1, 1)))
    assert total == 0


async def test_date_to_today_includes_today_record(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """date_to=today → 推進至明日 00:00 UTC，當天建立的 record 仍在範圍內。"""
    svc = _svc(db_session)
    actor = await _actor(db_session, "df3")
    created = await svc.create_record(
        RecordCreate(title="today", value=1.0, category="感測器"), actor
    )
    today = date.today()
    rows, total, _, _ = await svc.list_records(**_list_kwargs(date_to=today))
    assert total >= 1
    assert any(r.record.id == created.record.id for r in rows)


async def test_analytics_max_size_with_date_range(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """date_from 存在時用 analytics_max（5000），無日期範圍用 list_max（100）。"""
    svc = _svc(db_session)
    today = date.today()
    _, _, _, size_analytics = await svc.list_records(**_list_kwargs(size=1000, date_from=today))
    _, _, _, size_list = await svc.list_records(**_list_kwargs(size=1000))
    assert size_analytics == 1000  # 1000 ≤ analytics_max=5000，不夾
    assert size_list == 100  # 夾至 list_max=100
