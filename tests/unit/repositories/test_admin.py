"""AdminRepository + admins 表 DB 完整性（email unique、複合 FK 錯配）。§8.2。"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.models import Admin, Principal
from app.repositories.admin import AdminRepository
from app.repositories.principal import PrincipalRepository


async def _make_admin(db_session: AsyncSession, *, email: str, name: str = "A") -> Admin:
    principal: Principal = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin: Admin = Admin(
        email=email, name=name, password_hash="$argon2id$stub", principal_id=principal.id
    )
    db_session.add(admin)
    await db_session.flush()
    return admin


async def test_get_by_email_returns_admin(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    await _make_admin(db_session, email="found@example.com")

    result = await repo.get_by_email("found@example.com")

    assert result is not None
    assert result.email == "found@example.com"


async def test_get_by_email_returns_none_when_missing(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    assert await repo.get_by_email("nobody@example.com") is None


async def test_get_by_principal_id_returns_admin(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    admin = await _make_admin(db_session, email="pid@example.com")

    result = await repo.get_by_principal_id(admin.principal_id)

    assert result is not None
    assert result.id == admin.id


async def test_admin_email_unique(db_session: AsyncSession) -> None:
    await _make_admin(db_session, email="clash@example.com")

    with pytest.raises(IntegrityError):
        await _make_admin(db_session, email="clash@example.com")


async def test_composite_fk_rejects_admin_on_user_role_principal(db_session: AsyncSession) -> None:
    """Admin（role 恆 1）掛到 role=0 的 principal → 複合 FK 擋下。"""
    user_principal: Principal = await PrincipalRepository(db_session).create(Role.USER)
    admin: Admin = Admin(
        email="bad@example.com",
        name="Bad",
        password_hash="$argon2id$stub",
        principal_id=user_principal.id,
    )
    db_session.add(admin)

    with pytest.raises(IntegrityError):
        await db_session.flush()
