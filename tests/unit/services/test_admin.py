"""Unit tests for AdminService — create（建 principal+admin、argon2）/ 查詢 / delete（CASCADE）。

見 docs/specs/jwt-role-and-admin.md §5.5、§8.2。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models import Admin, RefreshToken
from app.repositories.principal import PrincipalRepository
from app.services.admin import AdminService


async def test_create_admin_builds_principal_role1_and_hashes_password(
    db_session: AsyncSession,
) -> None:
    svc: AdminService = AdminService(db_session)

    admin: Admin = await svc.create(email="admin@example.com", name="Root", password="longpassword")

    assert admin.id is not None
    assert admin.principal_id is not None
    assert admin.role == 1
    assert admin.password_hash != "longpassword"
    assert admin.password_hash.startswith("$argon2id$")
    principal = await PrincipalRepository(db_session).get(admin.principal_id)
    assert principal is not None
    assert principal.role == 1


async def test_get_by_email_and_principal_id(db_session: AsyncSession) -> None:
    svc: AdminService = AdminService(db_session)
    created: Admin = await svc.create(email="a2@example.com", name="A2", password="longpassword")

    by_email = await svc.get_by_email("a2@example.com")
    by_pid = await svc.get_by_principal_id(created.principal_id)

    assert by_email is not None
    assert by_email.id == created.id
    assert by_pid is not None
    assert by_pid.id == created.id


async def test_create_duplicate_email_raises_conflict(db_session: AsyncSession) -> None:
    svc: AdminService = AdminService(db_session)
    await svc.create(email="dup@example.com", name="D", password="longpassword")

    with pytest.raises(ConflictError):
        await svc.create(email="dup@example.com", name="D2", password="longpassword")


async def test_delete_admin_cascades_refresh_tokens(db_session: AsyncSession) -> None:
    """delete(admin_id) → 刪 principal，CASCADE 連帶清 admin + 其 refresh_tokens。"""
    svc: AdminService = AdminService(db_session)
    admin: Admin = await svc.create(email="del@example.com", name="Del", password="longpassword")
    token: RefreshToken = RefreshToken(
        principal_id=admin.principal_id,
        token_hash="admin-cascade-hash",
        family_id="fam-admin",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(token)
    await db_session.commit()

    await svc.delete(admin.id)

    admin_left = (
        await db_session.execute(select(Admin).where(Admin.id == admin.id))
    ).scalar_one_or_none()
    token_left = (
        await db_session.execute(
            select(RefreshToken).where(RefreshToken.principal_id == admin.principal_id)
        )
    ).scalar_one_or_none()
    assert admin_left is None
    assert token_left is None
    # session.get 也須回 None（identity map 不得留 stale 快取）
    assert await svc.repo.get(admin.id) is None
