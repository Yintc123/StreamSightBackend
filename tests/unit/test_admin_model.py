"""Admin model：is_active 計算屬性 + admin_role 預設/CHECK 值域。§8.1。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole, Role
from app.models import Admin, Principal
from app.repositories.principal import PrincipalRepository


async def _make_admin(
    db_session: AsyncSession, *, username: str, admin_role: int | None = None
) -> Admin:
    principal: Principal = await PrincipalRepository(db_session).create(Role.ADMIN)
    kwargs: dict = {}
    if admin_role is not None:
        kwargs["admin_role"] = admin_role
    admin: Admin = Admin(
        username=username,
        name="A",
        password_hash="$argon2id$stub",
        principal_id=principal.id,
        **kwargs,
    )
    db_session.add(admin)
    await db_session.flush()
    return admin


def test_is_active_true_when_both_timestamps_none() -> None:
    admin = Admin(username="a", name="A", password_hash="h", principal_id=1)
    admin.archived_at = None
    admin.deleted_at = None
    assert admin.is_active is True


def test_is_active_false_when_archived() -> None:
    admin = Admin(username="a", name="A", password_hash="h", principal_id=1)
    admin.archived_at = datetime.now(UTC)
    admin.deleted_at = None
    assert admin.is_active is False


def test_is_active_false_when_deleted() -> None:
    admin = Admin(username="a", name="A", password_hash="h", principal_id=1)
    admin.archived_at = None
    admin.deleted_at = datetime.now(UTC)
    assert admin.is_active is False


def test_is_active_false_when_both_set() -> None:
    admin = Admin(username="a", name="A", password_hash="h", principal_id=1)
    admin.archived_at = datetime.now(UTC)
    admin.deleted_at = datetime.now(UTC)
    assert admin.is_active is False


async def test_admin_role_defaults_to_viewer(db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session, username="def")
    # 重新讀取以確認 server_default 落地
    fetched = (await db_session.execute(select(Admin).where(Admin.id == admin.id))).scalar_one()
    assert fetched.admin_role == AdminRole.VIEWER.value


async def test_admin_role_check_rejects_out_of_domain(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await _make_admin(db_session, username="bad", admin_role=3)  # 非 (0,50,100,999)


# ── is_protected + 兩條單列 CHECK（admin-management-model §2.3/§7.1）──


async def _make_protected(
    db_session: AsyncSession,
    *,
    username: str,
    admin_role: int = AdminRole.ROOT.value,
    archived: bool = False,
    deleted: bool = False,
) -> Admin:
    principal: Principal = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin = Admin(
        username=username,
        name="A",
        password_hash="h",
        principal_id=principal.id,
        admin_role=admin_role,
        is_protected=True,
    )
    if archived:
        admin.archived_at = datetime.now(UTC)
    if deleted:
        admin.deleted_at = datetime.now(UTC)
    db_session.add(admin)
    await db_session.flush()
    return admin


async def test_is_protected_defaults_to_false(db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session, username="def2")
    fetched = (await db_session.execute(select(Admin).where(Admin.id == admin.id))).scalar_one()
    assert fetched.is_protected is False


async def test_protected_must_be_root(db_session: AsyncSession) -> None:
    # ck_admins_protected_is_super：protected 且非 ROOT(999) → IntegrityError
    with pytest.raises(IntegrityError):
        await _make_protected(db_session, username="p1", admin_role=AdminRole.SUPER_ADMIN.value)


async def test_protected_root_writes_ok(db_session: AsyncSession) -> None:
    admin = await _make_protected(db_session, username="p2", admin_role=AdminRole.ROOT.value)
    assert admin.is_protected is True


async def test_protected_must_be_active_archived(db_session: AsyncSession) -> None:
    # ck_admins_protected_is_active：protected 且已封存 → IntegrityError
    with pytest.raises(IntegrityError):
        await _make_protected(db_session, username="p3", archived=True)


async def test_protected_must_be_active_deleted(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await _make_protected(db_session, username="p4", deleted=True)
