"""Integration tests for /users（自助資源）。

授權模型：`/users/*` 由**使用者自己的 access token** 存取，且**只能存取自己**（self-scoped，
與 admin 無關）。非本人 → 403。`GET /users`（列全部）與 `POST /users`（建立）**已移除**：
註冊一律走 `/auth/register`（會建 user+identity+發 token）。
"""

from typing import Any
from uuid import UUID

from fastapi import status
from httpx import AsyncClient, Response

from app.core.exceptions import ForbiddenError


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(
    client: AsyncClient, email: str, name: str = "U", pw: str = "longpassword"
) -> tuple[str, int]:
    """經 /auth/register 建帳號，回 (access_token, user_id)。"""
    resp: Response = await client.post(
        "/auth/register", json={"email": email, "name": name, "password": pw}
    )
    assert resp.status_code == status.HTTP_201_CREATED
    token: str = resp.json()["access_token"]
    me: Response = await client.get("/users/me", headers=_auth(token))
    return token, me.json()["id"]


# ── 讀取：本人可讀、他人 403、未認證 401 ──


async def test_get_self_returns_200(client: AsyncClient) -> None:
    token, uid = await _register(client, "self@example.com", name="Self")
    resp: Response = await client.get(f"/users/{uid}", headers=_auth(token))

    assert resp.status_code == status.HTTP_200_OK
    data: dict[str, Any] = resp.json()
    assert data["id"] == uid
    assert data["email"] == "self@example.com"


async def test_get_other_user_forbidden(client: AsyncClient) -> None:
    token_a, _ = await _register(client, "a@example.com", name="A")
    _, uid_b = await _register(client, "b@example.com", name="B")

    resp: Response = await client.get(f"/users/{uid_b}", headers=_auth(token_a))

    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["error"] == ForbiddenError.error_code


async def test_get_user_unauthenticated_401(client: AsyncClient) -> None:
    assert (await client.get("/users/1")).status_code == status.HTTP_401_UNAUTHORIZED


# ── 更新：本人可改、他人 403、未認證 401 ──


async def test_patch_self_updates_fields(client: AsyncClient) -> None:
    token, uid = await _register(client, "patch@example.com", name="P")

    resp: Response = await client.patch(
        f"/users/{uid}", json={"name": "renamed"}, headers=_auth(token)
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["name"] == "renamed"


async def test_patch_other_user_forbidden(client: AsyncClient) -> None:
    token_a, _ = await _register(client, "pa@example.com", name="A")
    _, uid_b = await _register(client, "pb@example.com", name="B")

    resp: Response = await client.patch(
        f"/users/{uid_b}", json={"name": "hax"}, headers=_auth(token_a)
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_patch_unauthenticated_401(client: AsyncClient) -> None:
    resp: Response = await client.patch("/users/1", json={"name": "x"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── 刪除：本人可刪、他人 403、未認證 401 ──


async def test_delete_self_returns_204(client: AsyncClient) -> None:
    token, uid = await _register(client, "del@example.com", name="D")

    resp: Response = await client.delete(f"/users/{uid}", headers=_auth(token))
    assert resp.status_code == status.HTTP_204_NO_CONTENT
    assert resp.content == b""

    # 帳號已刪 → 同一 token 打 /me 應 401（get_current_user 每請求重查 DB）
    me: Response = await client.get("/users/me", headers=_auth(token))
    assert me.status_code == status.HTTP_401_UNAUTHORIZED


async def test_delete_other_user_forbidden(client: AsyncClient) -> None:
    token_a, _ = await _register(client, "da@example.com", name="A")
    _, uid_b = await _register(client, "db@example.com", name="B")

    resp: Response = await client.delete(f"/users/{uid_b}", headers=_auth(token_a))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_delete_unauthenticated_401(client: AsyncClient) -> None:
    assert (await client.delete("/users/1")).status_code == status.HTTP_401_UNAUTHORIZED


# ── 已移除端點：列表與建立 ──


async def test_list_users_endpoint_removed(client: AsyncClient) -> None:
    """GET /users（列全部）已移除，自助模型無此語意。"""
    resp: Response = await client.get("/users")
    assert resp.status_code == status.HTTP_404_NOT_FOUND


async def test_create_user_endpoint_removed(client: AsyncClient) -> None:
    """POST /users 已移除；註冊走 /auth/register。"""
    resp: Response = await client.post("/users", json={"email": "x@example.com", "name": "X"})
    assert resp.status_code == status.HTTP_404_NOT_FOUND


# ── /users/me（本人）──


async def test_users_me_exposes_tier(client: AsyncClient) -> None:
    """rbac §5.2：/me 曝露 user_tier（前端等級真實來源，反映 DB 現值）。"""
    reg: Response = await client.post(
        "/auth/register",
        json={"email": "tier@example.com", "name": "T", "password": "longpassword"},
    )
    token: str = reg.json()["access_token"]
    me: Response = await client.get("/users/me", headers=_auth(token))
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["tier"] == "free"


async def test_users_me_without_token_returns_401(client: AsyncClient) -> None:
    """沒帶 Authorization header → 401 (OAuth2PasswordBearer 自動拒絕)。"""
    response: Response = await client.get("/users/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_users_me_with_invalid_token_returns_401(client: AsyncClient) -> None:
    """亂 token → 401、走我們的 UnauthorizedError shape。"""
    response: Response = await client.get(
        "/users/me", headers={"Authorization": "Bearer not.a.real.token"}
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data: dict[str, Any] = response.json()
    assert data["error"] == "unauthorized"
    assert data["message"] == "Invalid token"


# ── request_id 傳播（走本人 403 錯誤路徑，確保經全域 handler）──


async def test_request_id_propagates(client: AsyncClient) -> None:
    """Custom X-Request-ID should be echoed and appear in error responses."""
    token_a, _ = await _register(client, "rid-a@example.com", name="A")
    _, uid_b = await _register(client, "rid-b@example.com", name="B")
    request_id: str = "test-req-abc"

    response: Response = await client.get(
        f"/users/{uid_b}",
        headers={"X-Request-ID": request_id, **_auth(token_a)},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.headers["x-request-id"] == request_id
    assert response.json()["request_id"] == request_id


async def test_request_id_auto_generated(client: AsyncClient) -> None:
    """Without X-Request-ID, one should be auto-generated as UUID4."""
    token_a, _ = await _register(client, "rid2-a@example.com", name="A")
    _, uid_b = await _register(client, "rid2-b@example.com", name="B")

    response: Response = await client.get(f"/users/{uid_b}", headers=_auth(token_a))

    request_id: str | None = response.headers.get("x-request-id")
    assert request_id is not None
    assert UUID(request_id).version == 4
