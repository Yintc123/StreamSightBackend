"""Integration tests for /auth/* endpoints."""

from typing import Any

from fastapi import status
from httpx import AsyncClient, Response

from app.core.exceptions import ConflictError, SystemErrorCode, UnauthorizedError


async def test_register_returns_201_with_token(client: AsyncClient) -> None:
    response: Response = await client.post(
        "/auth/register",
        json={"email": "new@example.com", "name": "New", "password": "longpassword"},
    )

    assert response.status_code == status.HTTP_201_CREATED
    data: dict[str, Any] = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_register_duplicate_email_returns_409(client: AsyncClient) -> None:
    payload: dict[str, str] = {
        "email": "dup@example.com",
        "name": "A",
        "password": "longpassword",
    }
    await client.post("/auth/register", json=payload)

    response: Response = await client.post("/auth/register", json=payload)

    assert response.status_code == status.HTTP_409_CONFLICT
    data: dict[str, Any] = response.json()
    assert data["error"] == ConflictError.error_code


async def test_register_short_password_returns_422(client: AsyncClient) -> None:
    response: Response = await client.post(
        "/auth/register",
        json={"email": "short@example.com", "name": "S", "password": "short"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    data: dict[str, Any] = response.json()
    assert data["error"] == SystemErrorCode.VALIDATION_ERROR


async def test_register_invalid_email_returns_422(client: AsyncClient) -> None:
    response: Response = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "name": "X", "password": "longpassword"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_login_correct_credentials_returns_200_with_token(client: AsyncClient) -> None:
    await client.post(
        "/auth/register",
        json={"email": "login@example.com", "name": "L", "password": "longpassword"},
    )

    response: Response = await client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "longpassword"},
    )

    assert response.status_code == status.HTTP_200_OK
    data: dict[str, Any] = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    await client.post(
        "/auth/register",
        json={"email": "wp@example.com", "name": "W", "password": "longpassword"},
    )

    response: Response = await client.post(
        "/auth/login",
        json={"email": "wp@example.com", "password": "WRONG"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data: dict[str, Any] = response.json()
    assert data["error"] == UnauthorizedError.error_code
    assert data["message"] == "Invalid email or password"


async def test_login_nonexistent_email_returns_401(client: AsyncClient) -> None:
    """防 user enumeration：跟 wrong password 統一訊息。"""
    response: Response = await client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "anypassword"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data: dict[str, Any] = response.json()
    assert data["message"] == "Invalid email or password"


async def test_full_register_login_me_flow(client: AsyncClient) -> None:
    """端到端：register → 拿 token → 打 /users/me → 拿到 user。"""
    # 1. Register
    reg_resp: Response = await client.post(
        "/auth/register",
        json={"email": "e2e@example.com", "name": "E2E", "password": "longpassword"},
    )
    assert reg_resp.status_code == status.HTTP_201_CREATED
    token: str = reg_resp.json()["access_token"]

    # 2. 用 token 打 /users/me
    me_resp: Response = await client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == status.HTTP_200_OK
    me_data: dict[str, Any] = me_resp.json()
    assert me_data["email"] == "e2e@example.com"
    assert me_data["name"] == "E2E"

    # 3. Login 再拿一個 token、也要能打 /users/me
    login_resp: Response = await client.post(
        "/auth/login",
        json={"email": "e2e@example.com", "password": "longpassword"},
    )
    assert login_resp.status_code == status.HTTP_200_OK
    login_token: str = login_resp.json()["access_token"]

    me_resp2: Response = await client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {login_token}"},
    )
    assert me_resp2.status_code == status.HTTP_200_OK
