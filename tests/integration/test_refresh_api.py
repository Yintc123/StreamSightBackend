"""Integration tests for refresh token endpoints. Spec §8.4.

covers /auth/refresh、/auth/logout、/auth/logout-all，rotation 鏈、
reuse detection（含 grace 良性路徑）、expires_in。
"""

from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient, Response

from app.core.config import get_app_settings


async def _register(client: AsyncClient, email: str = "r@example.com") -> dict[str, Any]:
    resp: Response = await client.post(
        "/auth/register",
        json={"email": email, "name": "R", "password": "longpassword"},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    return resp.json()


def _set_grace(monkeypatch: pytest.MonkeyPatch, seconds: int) -> None:
    """Override reuse grace on the cached settings singleton for this test."""
    settings = get_app_settings()
    monkeypatch.setattr(settings, "refresh_token_reuse_grace_seconds", seconds)


async def _refresh(client: AsyncClient, refresh_token: str) -> Response:
    return await client.post("/auth/refresh", json={"refresh_token": refresh_token})


# ── login/register 回應內容 ─────────────────────────────────
async def test_login_response_has_refresh_and_expires_in(client: AsyncClient) -> None:
    await _register(client, "login-resp@example.com")
    resp: Response = await client.post(
        "/auth/login",
        json={"email": "login-resp@example.com", "password": "longpassword"},
    )
    assert resp.status_code == status.HTTP_200_OK
    data: dict[str, Any] = resp.json()
    assert data["refresh_token"]
    assert data["expires_in"] == get_app_settings().jwt_access_token_expire_seconds


# ── refresh：rotation ───────────────────────────────────────
async def test_refresh_returns_new_tokens(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_grace(monkeypatch, 0)
    r1: str = (await _register(client, "rot@example.com"))["refresh_token"]

    resp: Response = await _refresh(client, r1)
    assert resp.status_code == status.HTTP_200_OK
    data: dict[str, Any] = resp.json()
    assert data["access_token"]
    assert data["refresh_token"] and data["refresh_token"] != r1
    assert data["expires_in"] == get_app_settings().jwt_access_token_expire_seconds

    # 舊 token 再用（grace=0，超過 grace）→ 401
    assert (await _refresh(client, r1)).status_code == status.HTTP_401_UNAUTHORIZED


async def test_rotation_chain(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_grace(monkeypatch, 0)
    r1: str = (await _register(client, "chain@example.com"))["refresh_token"]

    resp2: Response = await _refresh(client, r1)
    assert resp2.status_code == status.HTTP_200_OK
    r2: str = resp2.json()["refresh_token"]

    resp3: Response = await _refresh(client, r2)
    assert resp3.status_code == status.HTTP_200_OK
    r3: str = resp3.json()["refresh_token"]

    assert (await _refresh(client, r3)).status_code == status.HTTP_200_OK


async def test_reuse_beyond_grace_nukes_family(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_grace(monkeypatch, 0)
    r1: str = (await _register(client, "reuse@example.com"))["refresh_token"]

    r2: str = (await _refresh(client, r1)).json()["refresh_token"]
    # 重用已輪替的舊 token（超過 grace）→ 401
    assert (await _refresh(client, r1)).status_code == status.HTTP_401_UNAUTHORIZED
    # family 連坐：最新 token 也失效
    assert (await _refresh(client, r2)).status_code == status.HTTP_401_UNAUTHORIZED


async def test_reuse_within_grace_does_not_nuke_family(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_grace(monkeypatch, 3600)
    r1: str = (await _register(client, "grace@example.com"))["refresh_token"]

    r2: str = (await _refresh(client, r1)).json()["refresh_token"]
    # 剛輪替後重放舊 token（grace 內）→ 401，但不連坐
    assert (await _refresh(client, r1)).status_code == status.HTTP_401_UNAUTHORIZED
    # 最新 token 仍可繼續 refresh
    assert (await _refresh(client, r2)).status_code == status.HTTP_200_OK


# ── logout ──────────────────────────────────────────────────
async def test_logout_then_refresh_401(client: AsyncClient) -> None:
    r1: str = (await _register(client, "logout@example.com"))["refresh_token"]

    logout_resp: Response = await client.post("/auth/logout", json={"refresh_token": r1})
    assert logout_resp.status_code == status.HTTP_204_NO_CONTENT

    assert (await _refresh(client, r1)).status_code == status.HTTP_401_UNAUTHORIZED


# ── logout-all ──────────────────────────────────────────────
async def test_logout_all_revokes_all_devices(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_grace(monkeypatch, 0)
    await _register(client, "multi@example.com")

    # 多裝置 = 多次 login
    login_a = (
        await client.post(
            "/auth/login", json={"email": "multi@example.com", "password": "longpassword"}
        )
    ).json()
    login_b = (
        await client.post(
            "/auth/login", json={"email": "multi@example.com", "password": "longpassword"}
        )
    ).json()
    access: str = login_a["access_token"]

    resp: Response = await client.post(
        "/auth/logout-all", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    assert (await _refresh(client, login_a["refresh_token"])).status_code == 401
    assert (await _refresh(client, login_b["refresh_token"])).status_code == 401


async def test_logout_all_requires_auth(client: AsyncClient) -> None:
    resp: Response = await client.post("/auth/logout-all")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── validation ──────────────────────────────────────────────
async def test_refresh_missing_field_422(client: AsyncClient) -> None:
    resp: Response = await client.post("/auth/refresh", json={})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
