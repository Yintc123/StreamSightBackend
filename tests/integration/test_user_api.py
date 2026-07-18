from typing import Any
from uuid import UUID

from fastapi import status
from httpx import AsyncClient, Response

from app.core.exceptions import ConflictError, NotFoundError, SystemErrorCode
from app.models import User
from tests.payloads import invalid_payload, user_payload


async def test_create_user_return_201(client: AsyncClient) -> None:
    payload: dict[str, Any] = user_payload("yin")
    response: Response = await client.post("/users", json=payload)

    assert response.status_code == status.HTTP_201_CREATED
    data: dict[str, Any] = response.json()
    assert data["email"] == payload["email"]
    assert data["name"] == payload["name"]
    assert data["is_active"] is True
    assert isinstance(data["id"], int)
    assert "created_at" in data


async def test_get_user_return_200(client: AsyncClient) -> None:
    payload: dict[str, Any] = user_payload("yin")
    create_resp: Response = await client.post("/users", json=payload)
    user_id: int = create_resp.json()["id"]

    response: Response = await client.get(f"/users/{user_id}")

    assert response.status_code == status.HTTP_200_OK
    data: dict[str, Any] = response.json()
    assert data["email"] == payload["email"]


async def test_get_nonexistent_returns_404(client: AsyncClient) -> None:
    response: Response = await client.get("/users/99999")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    data: dict[str, Any] = response.json()
    assert data["error"] == NotFoundError.error_code
    assert "not found" in data["message"]
    assert "request_id" in data


async def test_create_duplicate_email_returns_409(client: AsyncClient) -> None:
    await client.post("/users", json=user_payload("yin"))

    # 同 email 但改 name → 觸發 email unique 衝突
    duplicate: dict[str, Any] = user_payload("yin", name="yin2")
    response: Response = await client.post("/users", json=duplicate)

    assert response.status_code == status.HTTP_409_CONFLICT
    data: dict[str, Any] = response.json()
    assert data["error"] == ConflictError.error_code
    assert data["details"] == {"field": "email"}


async def test_create_invalid_email_returns_422(client: AsyncClient) -> None:
    payload: dict[str, Any] = invalid_payload("invalid_email")
    response: Response = await client.post("/users", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    data: dict[str, Any] = response.json()
    assert data["error"] == SystemErrorCode.VALIDATION_ERROR
    assert "details" in data


async def test_patch_updates_fields(client: AsyncClient) -> None:
    payload: dict[str, Any] = user_payload("yin")
    create_resp: Response = await client.post("/users", json=payload)
    user_id: int = create_resp.json()["id"]

    response: Response = await client.patch(
        f"/users/{user_id}",
        json={"name": "yin renamed"},
    )

    assert response.status_code == status.HTTP_200_OK
    data: dict[str, Any] = response.json()
    assert data["name"] == "yin renamed"
    assert data["email"] == payload["email"]


async def test_patch_deactivate_user(client: AsyncClient) -> None:
    """PATCH is_active=false should deactivate the account."""
    payload: dict[str, Any] = user_payload("yin")
    create_resp: Response = await client.post("/users", json=payload)
    user_id: int = create_resp.json()["id"]

    response: Response = await client.patch(
        f"/users/{user_id}",
        json={"is_active": False},
    )

    assert response.status_code == status.HTTP_200_OK
    data: dict[str, Any] = response.json()
    assert data["is_active"] is False


async def test_delete_returns_204(client: AsyncClient) -> None:
    payload: dict[str, Any] = user_payload("yin")
    create_resp: Response = await client.post("/users", json=payload)
    user_id: int = create_resp.json()["id"]

    response: Response = await client.delete(f"/users/{user_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""

    # 再讀取應該要是 404
    get_resp: Response = await client.get(f"/users/{user_id}")
    assert get_resp.status_code == status.HTTP_404_NOT_FOUND


async def test_request_id_propagates(client: AsyncClient) -> None:
    """Custom X-Request-ID should be echoed and appear in error responses."""
    request_id: str = "test-req-abc"

    response: Response = await client.get(
        "/users/99999",
        headers={"X-Request-ID": request_id},
    )

    assert response.headers["x-request-id"] == request_id
    data: dict[str, Any] = response.json()
    assert data["request_id"] == request_id


async def test_request_id_auto_generated(client: AsyncClient) -> None:
    """Without X-Request-ID, one should be auto-generated as UUID4."""
    response: Response = await client.get("/users/99999")

    request_id: str | None = response.headers.get("x-request-id")
    assert request_id is not None
    # 與 middleware 契約：str(uuid4())
    assert UUID(request_id).version == 4


async def test_list_users(client: AsyncClient, sample_users: list[User]) -> None:
    response: Response = await client.get("/users")

    assert response.status_code == status.HTTP_200_OK
    users: list[dict[str, Any]] = response.json()
    assert len(users) == len(sample_users)


async def test_users_me_exposes_tier(client: AsyncClient) -> None:
    """rbac §5.2：/me 曝露 user_tier（前端等級真實來源，反映 DB 現值）。"""
    reg: Response = await client.post(
        "/auth/register",
        json={"email": "tier@example.com", "name": "T", "password": "longpassword"},
    )
    token: str = reg.json()["access_token"]
    me: Response = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["tier"] == "free"


async def test_users_me_without_token_returns_401(client: AsyncClient) -> None:
    """沒帶 Authorization header → 401 (OAuth2PasswordBearer 自動拒絕)。"""
    response: Response = await client.get("/users/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_users_me_with_invalid_token_returns_401(client: AsyncClient) -> None:
    """亂 token → 401、走我們的 UnauthorizedError shape。"""
    response: Response = await client.get(
        "/users/me",
        headers={"Authorization": "Bearer not.a.real.token"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data: dict[str, Any] = response.json()
    assert data["error"] == "unauthorized"
    assert data["message"] == "Invalid token"
