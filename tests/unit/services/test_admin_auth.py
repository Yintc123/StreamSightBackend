"""Unit tests for admin login（username）+ admin-aware refresh + 常數時間。§8.3。

admin_login 發 role=1 token；refresh 依 principal.role 重簽（admin 仍 role 1）。
封存／軟刪除的 admin 一律不可登入／refresh（讀 is_active 計算屬性）。
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.exceptions import UnauthorizedError
from app.dtos import AdminLoginRequest, RefreshRequest
from app.services import AdminService, AuthService


async def _seed_admin(
    db_session: AsyncSession, *, username: str = "cms", password: str = "longpassword"
) -> None:
    await AdminService(db_session).create(username=username, name="CMS", password=password)


async def test_admin_login_issues_role1_token_and_refresh(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    auth = AuthService(db_session)

    resp = await auth.admin_login(AdminLoginRequest(username="cms", password="longpassword"))

    payload = decode_token(resp.access_token)
    assert payload["role"] == 1
    assert resp.refresh_token is not None
    admin = await AdminService(db_session).get_by_username("cms")
    assert admin is not None
    assert payload["sub"] == str(admin.principal_id)


async def test_admin_login_case_variant_succeeds(db_session: AsyncSession) -> None:
    """DTO 正規化：`Cms` → `cms` 命中。"""
    await _seed_admin(db_session)
    auth = AuthService(db_session)
    resp = await auth.admin_login(AdminLoginRequest(username="Cms", password="longpassword"))
    assert decode_token(resp.access_token)["role"] == 1


async def test_admin_login_wrong_password_raises(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    auth = AuthService(db_session)

    with pytest.raises(UnauthorizedError) as exc:
        await auth.admin_login(AdminLoginRequest(username="cms", password="WRONG_PASSWORD"))
    assert exc.value.message == "Invalid username or password"


async def test_admin_login_nonexistent_raises_and_runs_verify(db_session: AsyncSession) -> None:
    """不存在帳號 → 401，且仍呼叫 verify_password_or_dummy（常數時間，§8.3b）。"""
    auth = AuthService(db_session)

    with (
        patch(
            "app.services.auth.verify_password_or_dummy",
            new=AsyncMock(return_value=False),
        ) as spy,
        pytest.raises(UnauthorizedError),
    ):
        await auth.admin_login(AdminLoginRequest(username="nobody", password="longpassword"))
    spy.assert_awaited_once()


async def test_admin_login_archived_raises(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    admin = await AdminService(db_session).get_by_username("cms")
    assert admin is not None
    await AdminService(db_session).archive(admin.id)
    auth = AuthService(db_session)

    with pytest.raises(UnauthorizedError):
        await auth.admin_login(AdminLoginRequest(username="cms", password="longpassword"))


async def test_admin_login_soft_deleted_raises(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    admin = await AdminService(db_session).get_by_username("cms")
    assert admin is not None
    await AdminService(db_session).delete(admin.id)
    auth = AuthService(db_session)

    with pytest.raises(UnauthorizedError):
        await auth.admin_login(AdminLoginRequest(username="cms", password="longpassword"))


async def test_admin_refresh_stays_role1(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    auth = AuthService(db_session)
    login_resp = await auth.admin_login(AdminLoginRequest(username="cms", password="longpassword"))
    assert login_resp.refresh_token is not None

    refreshed = await auth.refresh(RefreshRequest(refresh_token=login_resp.refresh_token))

    assert decode_token(refreshed.access_token)["role"] == 1


async def test_admin_refresh_rejected_when_archived(db_session: AsyncSession) -> None:
    await _seed_admin(db_session)
    auth = AuthService(db_session)
    login_resp = await auth.admin_login(AdminLoginRequest(username="cms", password="longpassword"))
    assert login_resp.refresh_token is not None
    admin = await AdminService(db_session).get_by_username("cms")
    assert admin is not None
    await AdminService(db_session).archive(admin.id)

    with pytest.raises(UnauthorizedError):
        await auth.refresh(RefreshRequest(refresh_token=login_resp.refresh_token))
