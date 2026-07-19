"""Integration tests for admin auth + role authorization. §8.4.

covers /admin/auth/login（username）、/admin/me（get_current_admin）、跨角色 403、
admin 走角色無關 /auth/refresh、/auth/logout-all、封存／軟刪除後不可登入／refresh。
"""

from typing import Any

from fastapi import status
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.config import get_app_settings
from app.core.enums import AdminRole
from app.models import Admin
from app.services import AdminService
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _admin_login(
    client: AsyncClient, username: str = ADMIN_USERNAME, password: str = ADMIN_PASSWORD
) -> Response:
    return await client.post("/admin/auth/login", json={"username": username, "password": password})


async def _register_user(client: AsyncClient, email: str = "u@example.com") -> dict[str, Any]:
    resp: Response = await client.post(
        "/auth/register", json={"email": email, "name": "U", "password": "longpassword"}
    )
    assert resp.status_code == status.HTTP_201_CREATED
    return resp.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_admin_login_success(client: AsyncClient, admin: Admin) -> None:
    resp: Response = await _admin_login(client)

    assert resp.status_code == status.HTTP_200_OK
    data: dict[str, Any] = resp.json()
    assert data["refresh_token"]
    assert data["expires_in"] == get_app_settings().jwt_access_token_expire_seconds
    assert decode_token(data["access_token"])["role"] == 1


async def test_admin_login_case_variant_success(client: AsyncClient, admin: Admin) -> None:
    """大小寫變體（Root）亦成功（DTO 正規化）。"""
    resp: Response = await _admin_login(client, username="Root")
    assert resp.status_code == status.HTTP_200_OK


async def test_admin_login_wrong_password_401(client: AsyncClient, admin: Admin) -> None:
    resp: Response = await _admin_login(client, password="WRONG_PASSWORD")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_admin_me_returns_expected_shape(client: AsyncClient, admin: Admin) -> None:
    access: str = (await _admin_login(client)).json()["access_token"]

    resp: Response = await client.get("/admin/me", headers=_auth(access))

    assert resp.status_code == status.HTTP_200_OK
    body: dict[str, Any] = resp.json()
    assert set(body.keys()) == {"id", "username", "name", "admin_role"}
    assert body["username"] == ADMIN_USERNAME
    assert body["admin_role"] == 100
    assert "email" not in body
    assert "archived_at" not in body
    assert "deleted_at" not in body


async def test_admin_token_forbidden_on_user_endpoint(client: AsyncClient, admin: Admin) -> None:
    access: str = (await _admin_login(client)).json()["access_token"]

    resp: Response = await client.get("/users/me", headers=_auth(access))

    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_user_token_forbidden_on_admin_endpoint(client: AsyncClient) -> None:
    access: str = (await _register_user(client))["access_token"]

    resp: Response = await client.get("/admin/me", headers=_auth(access))

    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_admin_refresh_stays_admin(client: AsyncClient, admin: Admin) -> None:
    login: dict[str, Any] = (await _admin_login(client)).json()

    resp: Response = await client.post(
        "/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )

    assert resp.status_code == status.HTTP_200_OK
    assert decode_token(resp.json()["access_token"])["role"] == 1


async def test_admin_logout_all_revokes_refresh(client: AsyncClient, admin: Admin) -> None:
    login: dict[str, Any] = (await _admin_login(client)).json()

    logout: Response = await client.post("/auth/logout-all", headers=_auth(login["access_token"]))
    assert logout.status_code == status.HTTP_204_NO_CONTENT

    refreshed: Response = await client.post(
        "/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED


async def test_admin_logout_all_requires_auth(client: AsyncClient) -> None:
    resp: Response = await client.post("/auth/logout-all")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_archived_admin_cannot_login_or_refresh(
    client: AsyncClient, admin: Admin, db_session: AsyncSession
) -> None:
    login: dict[str, Any] = (await _admin_login(client)).json()
    svc = AdminService(db_session)
    # 封存前先自我降級（super_admin 須先降級才能封存，§3.5；self-demotion 允許）
    await svc.set_admin_role(
        admin.id, admin_role=AdminRole.VIEWER, actor_principal_id=admin.principal_id
    )
    await svc.archive(admin.id)

    assert (await _admin_login(client)).status_code == status.HTTP_401_UNAUTHORIZED
    refreshed: Response = await client.post(
        "/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED


async def test_soft_deleted_admin_cannot_login_or_refresh(
    client: AsyncClient, admin: Admin, db_session: AsyncSession
) -> None:
    login: dict[str, Any] = (await _admin_login(client)).json()
    svc = AdminService(db_session)
    # 軟刪除前先自我降級（super_admin 須先降級才能刪除，§3.5）
    await svc.set_admin_role(
        admin.id, admin_role=AdminRole.VIEWER, actor_principal_id=admin.principal_id
    )
    await svc.delete(admin.id)

    assert (await _admin_login(client)).status_code == status.HTTP_401_UNAUTHORIZED
    refreshed: Response = await client.post(
        "/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED
