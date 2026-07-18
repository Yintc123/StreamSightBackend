"""Initial super admin（憑證存 config／SSM、不進 DB、不可改密碼、恆可登入）。

取代舊 seed 腳本:第一位 super admin 就是這個 SSM 帳號,登入後即可建立 DB admin。
- 憑證來自 config（SSM 注入的 argon2 雜湊,INITIAL_ADMIN_USERNAME + INITIAL_ADMIN_PASSWORD_HASH）。
- 登入只發 access token（無 refresh;無 DB principal 可掛）。
- 是 super_admin,可管理其他 admin;但自己不可被管理／改密碼／鎖死。
"""

from collections.abc import AsyncGenerator

import pytest
from fastapi import status
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.auth import decode_token, hash_password
from app.core.config import get_app_settings

_IA_USERNAME = "root-init"
_IA_PASSWORD = "initial-strong-pw"


@pytest.fixture
async def initial_admin(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[dict[str, str]]:
    """啟用初始 super admin：把 config 的 username + argon2 雜湊 patch 進（模擬 SSM 注入）。"""
    settings = get_app_settings()
    pw_hash = await hash_password(_IA_PASSWORD)
    monkeypatch.setattr(settings, "initial_admin_username", _IA_USERNAME)
    monkeypatch.setattr(settings, "initial_admin_password_hash", SecretStr(pw_hash))
    yield {"username": _IA_USERNAME, "password": _IA_PASSWORD}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _ia_login(client: AsyncClient) -> dict:
    resp = await client.post(
        "/admin/auth/login", json={"username": _IA_USERNAME, "password": _IA_PASSWORD}
    )
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()


async def test_initial_admin_login_access_only_super_admin(
    client: AsyncClient, initial_admin: dict
) -> None:
    body = await _ia_login(client)
    assert body["access_token"]
    assert body["refresh_token"] is None  # 無 refresh（無 DB principal）
    payload = decode_token(body["access_token"])
    assert payload["role"] == 1
    assert payload["grade"] == "super_admin"
    assert payload["sub"] == "0"  # 哨兵 principal_id


async def test_initial_admin_wrong_password_401(client: AsyncClient, initial_admin: dict) -> None:
    resp = await client.post(
        "/admin/auth/login", json={"username": _IA_USERNAME, "password": "wrong-password"}
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_initial_admin_can_bootstrap_db_admins(
    client: AsyncClient, initial_admin: dict
) -> None:
    """第一位 super admin 用它登入後建立 DB admin（取代 seed）。"""
    token = (await _ia_login(client))["access_token"]
    listed = await client.get("/admin/admins", headers=_auth(token))
    assert listed.status_code == status.HTTP_200_OK
    created = await client.post(
        "/admin/admins",
        headers=_auth(token),
        json={"username": "first-db-admin", "name": "First", "password": "longpassword"},
    )
    assert created.status_code == status.HTTP_201_CREATED


async def test_initial_admin_me_returns_super_admin(
    client: AsyncClient, initial_admin: dict
) -> None:
    token = (await _ia_login(client))["access_token"]
    me = await client.get("/admin/me", headers=_auth(token))
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["admin_role"] == "super_admin"
    assert me.json()["username"] == _IA_USERNAME


async def test_initial_admin_default_name_is_username(
    client: AsyncClient, initial_admin: dict
) -> None:
    """未設 INITIAL_ADMIN_NAME → 顯示名稱用 username。"""
    token = (await _ia_login(client))["access_token"]
    me = await client.get("/admin/me", headers=_auth(token))
    assert me.json()["name"] == _IA_USERNAME


async def test_initial_admin_display_name_from_config(
    client: AsyncClient, initial_admin: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """設了 INITIAL_ADMIN_NAME → /admin/me 顯示該名稱。"""
    monkeypatch.setattr(get_app_settings(), "initial_admin_name", "Administrator")
    token = (await _ia_login(client))["access_token"]
    me = await client.get("/admin/me", headers=_auth(token))
    assert me.json()["name"] == "Administrator"
    assert me.json()["username"] == _IA_USERNAME  # username 不受影響


async def test_initial_admin_not_in_db_list(client: AsyncClient, initial_admin: dict) -> None:
    token = (await _ia_login(client))["access_token"]
    listed = await client.get("/admin/admins?status=all", headers=_auth(token))
    usernames = {i["username"] for i in listed.json()["items"]}
    assert _IA_USERNAME not in usernames  # 不在 admins 表


async def test_initial_admin_cannot_change_password(
    client: AsyncClient, initial_admin: dict
) -> None:
    token = (await _ia_login(client))["access_token"]
    resp = await client.post(
        "/admin/me/password",
        headers=_auth(token),
        json={"current_password": _IA_PASSWORD, "new_password": "anothernewpw1"},
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN  # 憑證由 SSM 管理，不可經 API 改


async def test_cannot_create_db_admin_with_initial_admin_username(
    client: AsyncClient, initial_admin: dict
) -> None:
    token = (await _ia_login(client))["access_token"]
    resp = await client.post(
        "/admin/admins",
        headers=_auth(token),
        json={"username": _IA_USERNAME, "name": "Clash", "password": "longpassword"},
    )
    assert resp.status_code == status.HTTP_409_CONFLICT  # 保留字


async def test_initial_admin_disabled_falls_through(client: AsyncClient) -> None:
    """未啟用（無 config）→ 用該 username 登入走 DB 路徑 → 查無 → 401。"""
    resp = await client.post(
        "/admin/auth/login", json={"username": _IA_USERNAME, "password": _IA_PASSWORD}
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
