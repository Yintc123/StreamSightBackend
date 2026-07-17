"""Unit tests for admin login + admin-aware refresh. §8.4.

admin_login 發 role=1 token；refresh 依 principal.role 重簽（admin 仍 role 1）。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.exceptions import UnauthorizedError
from app.dtos import LoginRequest, RefreshRequest
from app.services import AdminService, AuthService


async def _seed_admin(
    db_session: AsyncSession, *, email: str = "cms@example.com", password: str = "longpassword"
) -> None:
    await AdminService(db_session).create(email=email, name="CMS", password=password)


async def test_admin_login_issues_role1_token_and_refresh(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    auth: AuthService = AuthService(db_session)

    resp = await auth.admin_login(LoginRequest(email="cms@example.com", password="longpassword"))

    payload: dict = decode_token(resp.access_token)
    assert payload["role"] == 1
    assert resp.refresh_token is not None
    admin = await AdminService(db_session).get_by_email("cms@example.com")
    assert admin is not None
    assert payload["sub"] == str(admin.principal_id)


async def test_admin_login_wrong_password_raises(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    auth: AuthService = AuthService(db_session)

    with pytest.raises(UnauthorizedError) as exc:
        await auth.admin_login(LoginRequest(email="cms@example.com", password="WRONG_PASSWORD"))
    assert exc.value.message == "Invalid email or password"


async def test_admin_login_nonexistent_raises(db_session: AsyncSession) -> None:
    auth: AuthService = AuthService(db_session)

    with pytest.raises(UnauthorizedError) as exc:
        await auth.admin_login(LoginRequest(email="nobody@example.com", password="longpassword"))
    assert exc.value.message == "Invalid email or password"


async def test_admin_login_inactive_raises(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    admin = await AdminService(db_session).get_by_email("cms@example.com")
    assert admin is not None
    admin.is_active = False
    await db_session.commit()
    auth: AuthService = AuthService(db_session)

    with pytest.raises(UnauthorizedError):
        await auth.admin_login(LoginRequest(email="cms@example.com", password="longpassword"))


async def test_admin_refresh_stays_role1(db_session: AsyncSession) -> None:
    """admin 的 refresh token 走角色無關 refresh → 依 principal.role 重簽仍為 role 1。"""
    await _seed_admin(db_session)
    auth: AuthService = AuthService(db_session)
    login_resp = await auth.admin_login(
        LoginRequest(email="cms@example.com", password="longpassword")
    )
    assert login_resp.refresh_token is not None

    refreshed = await auth.refresh(RefreshRequest(refresh_token=login_resp.refresh_token))

    payload: dict = decode_token(refreshed.access_token)
    assert payload["role"] == 1


async def test_admin_refresh_rejected_when_inactive(db_session: AsyncSession) -> None:
    """停用 admin 的 refresh token 走角色無關 refresh → 讀 child.is_active 為 false → 401。§8.4。"""
    await _seed_admin(db_session)
    auth: AuthService = AuthService(db_session)
    login_resp = await auth.admin_login(
        LoginRequest(email="cms@example.com", password="longpassword")
    )
    assert login_resp.refresh_token is not None
    admin = await AdminService(db_session).get_by_email("cms@example.com")
    assert admin is not None
    admin.is_active = False
    await db_session.commit()

    with pytest.raises(UnauthorizedError):
        await auth.refresh(RefreshRequest(refresh_token=login_resp.refresh_token))
