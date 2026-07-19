"""RecordRepository：list/count/get_active/排序/篩選/LIKE ESCAPE（records-model.md §7.3）。"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole, RecordSortField, SortDirection
from app.models.admin import Admin
from app.models.record import Record
from app.models.record_category import RecordCategory
from app.repositories.repo_record import RecordRepository
from app.services import AdminService


async def _admin(db_session: AsyncSession, suffix: str) -> Admin:
    return await AdminService(db_session).create(
        username=f"rec-{suffix}", name="A", password="longpassword", admin_role=AdminRole.EDITOR
    )


async def _mk(
    db_session: AsyncSession,
    *,
    creator: Admin,
    category_id: int,
    title: str = "T",
    value: float = 1.0,
    deleted: bool = False,
    created_at: datetime | None = None,
) -> Record:
    rec = Record(
        title=title,
        value=value,
        category_id=category_id,
        created_by_principal_id=creator.principal_id,
    )
    if deleted:
        rec.deleted_at = datetime.now(UTC)
    if created_at is not None:
        rec.created_at = created_at
    db_session.add(rec)
    await db_session.flush()
    return rec


def _default_list_kwargs() -> dict:
    return {
        "category_id": None,
        "keyword": None,
        "date_from": None,
        "date_to": None,
        "sort_field": RecordSortField.ID,
        "sort_dir": SortDirection.ASC,
        "include_deleted": False,
        "limit": 100,
        "offset": 0,
    }


async def test_list_default_excludes_soft_deleted(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    a = await _admin(db_session, "c1")
    cid = record_categories[0].id
    await _mk(db_session, creator=a, category_id=cid, title="live")
    await _mk(db_session, creator=a, category_id=cid, title="gone", deleted=True)
    repo = RecordRepository(db_session)

    rows = await repo.list_records(**_default_list_kwargs())
    titles = [r.record.title for r in rows]
    assert "live" in titles and "gone" not in titles

    rows_all = await repo.list_records(**{**_default_list_kwargs(), "include_deleted": True})
    assert "gone" in [r.record.title for r in rows_all]


async def test_list_filter_category_and_keyword(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    a = await _admin(db_session, "c2")
    c0, c1 = record_categories[0].id, record_categories[1].id
    await _mk(db_session, creator=a, category_id=c0, title="Alpha")
    await _mk(db_session, creator=a, category_id=c1, title="Beta")
    repo = RecordRepository(db_session)

    rows = await repo.list_records(**{**_default_list_kwargs(), "category_id": c0})
    assert [r.record.title for r in rows] == ["Alpha"]

    # keyword 不分大小寫子字串（service 會 lower + wrap；此處直接給已處理值）
    rows_kw = await repo.list_records(**{**_default_list_kwargs(), "keyword": "%alp%"})
    assert [r.record.title for r in rows_kw] == ["Alpha"]


async def test_list_resolves_category_name_and_creator_username(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    a = await _admin(db_session, "resolver")
    await _mk(db_session, creator=a, category_id=record_categories[0].id, title="X")
    repo = RecordRepository(db_session)
    row = (await repo.list_records(**_default_list_kwargs()))[0]
    assert row.category_name == record_categories[0].name
    assert row.created_by_username == "rec-resolver"


async def test_list_filter_inactive_category_still_returns(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """repo 不判 active：以退場分類 id 篩選仍回其 records（§2.7-(1)）。"""
    a = await _admin(db_session, "c3")
    off = RecordCategory(name="退場", label="退場", is_active=False)
    db_session.add(off)
    await db_session.flush()
    await _mk(db_session, creator=a, category_id=off.id, title="old-data")
    repo = RecordRepository(db_session)

    rows = await repo.list_records(**{**_default_list_kwargs(), "category_id": off.id})
    assert [r.record.title for r in rows] == ["old-data"]


async def test_list_like_escape_treats_wildcard_literally(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """已跳脫的 \\% 只配字面含 % 的 title（驗 ESCAPE 生效，§2.7-(1)）。"""
    a = await _admin(db_session, "c4")
    cid = record_categories[0].id
    await _mk(db_session, creator=a, category_id=cid, title="50% off")
    await _mk(db_session, creator=a, category_id=cid, title="5000")
    repo = RecordRepository(db_session)

    rows = await repo.list_records(**{**_default_list_kwargs(), "keyword": r"%50\%%"})
    assert [r.record.title for r in rows] == ["50% off"]


async def test_sort_by_value_desc_with_id_tiebreaker(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    a = await _admin(db_session, "c5")
    cid = record_categories[0].id
    r1 = await _mk(db_session, creator=a, category_id=cid, title="a", value=5.0)
    r2 = await _mk(db_session, creator=a, category_id=cid, title="b", value=5.0)
    await _mk(db_session, creator=a, category_id=cid, title="c", value=1.0)
    repo = RecordRepository(db_session)

    rows = await repo.list_records(
        **{
            **_default_list_kwargs(),
            "sort_field": RecordSortField.VALUE,
            "sort_dir": SortDirection.DESC,
        }
    )
    # value desc；同值 5.0 以 id desc（r2 先於 r1）
    ids = [r.record.id for r in rows]
    assert ids[:2] == [r2.id, r1.id]


async def test_sort_by_category_uses_name_not_id(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """sort=category 依 record_categories.name 排序，非 category_id 插入序（§2.4）。"""
    a = await _admin(db_session, "c6")
    # 建兩個 name 排序與 id 插入序相反的分類
    z = RecordCategory(name="zzz", label="Z", sort_order=0)
    aa = RecordCategory(name="aaa", label="A", sort_order=0)
    db_session.add_all([z, aa])
    await db_session.flush()  # z.id < aa.id，但 name "aaa" < "zzz"
    await _mk(db_session, creator=a, category_id=z.id, title="in-zzz")
    await _mk(db_session, creator=a, category_id=aa.id, title="in-aaa")
    repo = RecordRepository(db_session)

    rows = await repo.list_records(
        **{
            **_default_list_kwargs(),
            "sort_field": RecordSortField.CATEGORY,
            "sort_dir": SortDirection.ASC,
        }
    )
    # 依 name asc：aaa 在前
    assert [r.record.title for r in rows] == ["in-aaa", "in-zzz"]


async def test_pagination_and_count_share_predicate(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    a = await _admin(db_session, "c7")
    cid = record_categories[0].id
    for i in range(5):
        await _mk(db_session, creator=a, category_id=cid, title=f"r{i}")
    repo = RecordRepository(db_session)

    page1 = await repo.list_records(**{**_default_list_kwargs(), "limit": 2, "offset": 0})
    page3 = await repo.list_records(**{**_default_list_kwargs(), "limit": 2, "offset": 4})
    assert len(page1) == 2
    assert len(page3) == 1  # 第 5 筆
    total = await repo.count_records(
        category_id=None, keyword=None, date_from=None, date_to=None, include_deleted=False
    )
    assert total == 5

    # 超末頁 → 空、total 不變
    beyond = await repo.list_records(**{**_default_list_kwargs(), "limit": 2, "offset": 10})
    assert beyond == []


async def test_get_active_returns_none_for_soft_deleted(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    a = await _admin(db_session, "c8")
    rec = await _mk(db_session, creator=a, category_id=record_categories[0].id, deleted=True)
    repo = RecordRepository(db_session)
    assert await repo.get_active(rec.id) is None
    assert await repo.get_active_row(rec.id) is None


# ── 日期範圍篩選（§2.7-(2)）──────────────────────────────────────


async def test_date_from_excludes_older_records(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """date_from → created_at >= :date_from；早於截止的記錄不回。"""
    a = await _admin(db_session, "dr1")
    cid = record_categories[0].id
    await _mk(
        db_session,
        creator=a,
        category_id=cid,
        title="old",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    await _mk(
        db_session,
        creator=a,
        category_id=cid,
        title="new",
        created_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    repo = RecordRepository(db_session)

    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    rows = await repo.list_records(**{**_default_list_kwargs(), "date_from": cutoff})
    assert [r.record.title for r in rows] == ["new"]


async def test_date_to_excludes_newer_records(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """date_to → created_at < :date_to（開區間右端）；晚於截止的記錄不回。"""
    a = await _admin(db_session, "dr2")
    cid = record_categories[0].id
    await _mk(
        db_session,
        creator=a,
        category_id=cid,
        title="old",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    await _mk(
        db_session,
        creator=a,
        category_id=cid,
        title="new",
        created_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    repo = RecordRepository(db_session)

    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    rows = await repo.list_records(**{**_default_list_kwargs(), "date_to": cutoff})
    assert [r.record.title for r in rows] == ["old"]


async def test_count_respects_date_range(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    """count_records 與 list_records 共用謂詞——日期範圍同樣生效。"""
    a = await _admin(db_session, "dr3")
    cid = record_categories[0].id
    await _mk(
        db_session,
        creator=a,
        category_id=cid,
        title="old",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    await _mk(
        db_session,
        creator=a,
        category_id=cid,
        title="new",
        created_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    repo = RecordRepository(db_session)

    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    count = await repo.count_records(
        category_id=None, keyword=None, date_from=cutoff, date_to=None, include_deleted=False
    )
    assert count == 1  # 只有 "new"
