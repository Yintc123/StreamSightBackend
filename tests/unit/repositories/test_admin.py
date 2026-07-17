"""AdminRepository + admins 表 DB 完整性（username unique、複合 FK 錯配）。§8.1。"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.models import Admin, Principal
from app.repositories.admin import AdminRepository
from app.repositories.principal import PrincipalRepository


async def _make_admin(db_session: AsyncSession, *, username: str, name: str = "A") -> Admin:
    principal: Principal = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin: Admin = Admin(
        username=username, name=name, password_hash="$argon2id$stub", principal_id=principal.id
    )
    db_session.add(admin)
    await db_session.flush()
    return admin


async def test_get_by_username_returns_admin(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    await _make_admin(db_session, username="found")

    result = await repo.get_by_username("found")

    assert result is not None
    assert result.username == "found"


async def test_get_by_username_returns_none_when_missing(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    assert await repo.get_by_username("nobody") is None


async def test_get_by_email_removed(db_session: AsyncSession) -> None:
    """get_by_email 已移除（§8.1）。"""
    repo: AdminRepository = AdminRepository(db_session)
    assert not hasattr(repo, "get_by_email")


async def test_get_by_principal_id_returns_admin(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    admin = await _make_admin(db_session, username="pid")

    result = await repo.get_by_principal_id(admin.principal_id)

    assert result is not None
    assert result.id == admin.id


async def test_admin_username_unique(db_session: AsyncSession) -> None:
    await _make_admin(db_session, username="clash")

    with pytest.raises(IntegrityError):
        await _make_admin(db_session, username="clash")


async def test_composite_fk_rejects_admin_on_user_role_principal(db_session: AsyncSession) -> None:
    """Admin（role 恆 1）掛到 role=0 的 principal → 複合 FK 擋下。"""
    user_principal: Principal = await PrincipalRepository(db_session).create(Role.USER)
    admin: Admin = Admin(
        username="bad",
        name="Bad",
        password_hash="$argon2id$stub",
        principal_id=user_principal.id,
    )
    db_session.add(admin)

    with pytest.raises(IntegrityError):
        await db_session.flush()
