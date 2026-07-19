"""Record model：FK（category / creator-admin）、title 非空 CHECK、預設、onupdate（§7.2）。"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole, Role
from app.models.admin import Admin
from app.models.record import Record
from app.models.record_category import RecordCategory
from app.repositories.repo_principal import PrincipalRepository
from app.services import AdminService


async def _make_admin(db_session: AsyncSession, username: str) -> Admin:
    return await AdminService(db_session).create(
        username=username, name="A", password="longpassword", admin_role=AdminRole.EDITOR
    )


async def _cat_id(db_session: AsyncSession, name: str = "感測器") -> int:
    cat = RecordCategory(name=name, label=name)
    db_session.add(cat)
    await db_session.flush()
    return cat.id


async def test_valid_record_writes_ok(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    admin = await _make_admin(db_session, "creator1")
    rec = Record(
        title="T",
        value=1.5,
        category_id=record_categories[0].id,
        created_by_principal_id=admin.principal_id,
    )
    db_session.add(rec)
    await db_session.flush()
    assert rec.id is not None
    assert rec.note == ""  # server_default
    assert rec.deleted_at is None
    assert rec.is_active is True


async def test_fk_records_category_rejects_unknown(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    admin = await _make_admin(db_session, "creator2")
    db_session.add(
        Record(title="T", value=1.0, category_id=99999, created_by_principal_id=admin.principal_id)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_ck_records_title_nonempty(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    admin = await _make_admin(db_session, "creator3")
    db_session.add(
        Record(
            title="",
            value=1.0,
            category_id=record_categories[0].id,
            created_by_principal_id=admin.principal_id,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_fk_creator_rejects_non_admin_principal(db_session: AsyncSession) -> None:
    """created_by_principal_id 指向非 admin 的 principal（role=0）→ IntegrityError（§2.1）。"""
    cat_id = await _cat_id(db_session)
    user_principal = await PrincipalRepository(db_session).create(Role.USER)
    db_session.add(
        Record(
            title="T",
            value=1.0,
            category_id=cat_id,
            created_by_principal_id=user_principal.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_updated_at_refreshes_on_update(
    db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    admin = await _make_admin(db_session, "creator4")
    rec = Record(
        title="Old",
        value=1.0,
        category_id=record_categories[0].id,
        created_by_principal_id=admin.principal_id,
    )
    db_session.add(rec)
    await db_session.flush()
    before = rec.updated_at

    rec.title = "New"
    await db_session.flush()
    await db_session.refresh(rec)
    assert rec.updated_at >= before
