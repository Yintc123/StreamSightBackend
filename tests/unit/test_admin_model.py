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
    db_session: AsyncSession, *, username: str, admin_role: str | None = None
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
        await _make_admin(db_session, username="bad", admin_role="root")
