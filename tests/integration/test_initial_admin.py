"""Bootstrap root admin — 開機 upsert 成**真實 DB admin**（bootstrap-hidden-admin.md）。

新模型（取代舊哨兵）：`ensure_initial_admin` 於啟動時以三個 env（USERNAME/PASSWORD/NAME）建立
一筆 `admin_role=ROOT(999)`、`is_protected=True` 的真實列（seed-once、冪等鍵＝有無 protected root）。
登入 / 授權 / 改密碼全走一般路徑——無哨兵、無 `sub==0` 特判。

初始 admin 專屬測試用不同 username（`bootstrapadmin`），不與 `admin` fixture（"root"）併用。
"""

from collections.abc import AsyncGenerator

import pytest
from fastapi import status
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_app_settings
from app.core.enums import AdminRole
from app.models.admin import Admin
from app.services.initial_admin import ensure_initial_admin

_IA_USERNAME = "bootstrapadmin"
_IA_PASSWORD = "initial-strong-pw"
_IA_NAME = "Administrator"


@pytest.fixture
def _patch_ia_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """把三個 bootstrap env patch 進 settings（模擬 env/SSM 注入），不落地。"""
    settings = get_app_settings()
    monkeypatch.setattr(settings, "initial_admin_username", _IA_USERNAME)
    monkeypatch.setattr(settings, "initial_admin_password", SecretStr(_IA_PASSWORD))
    monkeypatch.setattr(settings, "initial_admin_name", _IA_NAME)


@pytest.fixture
async def bootstrap_root(
    _patch_ia_config: None, db_session: AsyncSession
) -> AsyncGenerator[dict[str, str]]:
    """啟用並 seed 真實 root（ROOT=999、protected）到共享的 db_session。"""
    await ensure_initial_admin(db_session)
    yield {"username": _IA_USERNAME, "password": _IA_PASSWORD}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _get_root(db_session: AsyncSession) -> Admin | None:
    result = await db_session.execute(select(Admin).where(Admin.username == _IA_USERNAME))
    return result.scalar_one_or_none()


async def _login(client: AsyncClient) -> dict:
    resp = await client.post(
        "/admin/auth/login", json={"username": _IA_USERNAME, "password": _IA_PASSWORD}
    )
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()


# ── ensure_initial_admin：seed 行為 ──────────────────────────────
async def test_ensure_seeds_real_protected_root(
    _patch_ia_config: None, db_session: AsyncSession
) -> None:
    await ensure_initial_admin(db_session)
    root = await _get_root(db_session)
    assert root is not None
    assert root.admin_role == AdminRole.ROOT.value  # 999
    assert root.is_protected is True
    assert root.name == _IA_NAME


async def test_ensure_is_idempotent(_patch_ia_config: None, db_session: AsyncSession) -> None:
    """已有 protected root → 再次呼叫（即使換 username）不再建第二筆。"""
    await ensure_initial_admin(db_session)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(get_app_settings(), "initial_admin_username", "otherroot")
    try:
        await ensure_initial_admin(db_session)
    finally:
        monkeypatch.undo()
    count = len((await db_session.execute(select(Admin).where(Admin.is_protected))).all())
    assert count == 1


async def test_ensure_missing_env_fail_fast(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """三 env 任一為空 → 啟動 fail-fast（RuntimeError）。"""
    settings = get_app_settings()
    monkeypatch.setattr(settings, "initial_admin_username", "")
    monkeypatch.setattr(settings, "initial_admin_password", SecretStr(""))
    monkeypatch.setattr(settings, "initial_admin_name", "")
    with pytest.raises(RuntimeError):
        await ensure_initial_admin(db_session)


async def test_ensure_invalid_password_fail_fast(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """非空但不合法密碼（太短）→ fail-fast。"""
    settings = get_app_settings()
    monkeypatch.setattr(settings, "initial_admin_username", _IA_USERNAME)
    monkeypatch.setattr(settings, "initial_admin_password", SecretStr("123"))
    monkeypatch.setattr(settings, "initial_admin_name", _IA_NAME)
    with pytest.raises(RuntimeError):
        await ensure_initial_admin(db_session)


# ── seed 後：走一般登入 / 授權路徑 ───────────────────────────────
async def test_root_login_is_normal_with_refresh(client: AsyncClient, bootstrap_root: dict) -> None:
    """root 是真實 DB 列 → 一般登入（有 refresh token），/admin/me 顯示 ROOT=999。"""
    body = await _login(client)
    assert body["access_token"]
    assert body["refresh_token"] is not None  # 真實 principal → 有 refresh family
    token = body["access_token"]
    me = await client.get("/admin/me", headers=_auth(token))
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["admin_role"] == 999
    assert me.json()["username"] == _IA_USERNAME


async def test_root_can_change_own_password(client: AsyncClient, bootstrap_root: dict) -> None:
    """D1：root 可自管——改自己的密碼成功（204）。"""
    token = (await _login(client))["access_token"]
    resp = await client.post(
        "/admin/me/password",
        headers=_auth(token),
        json={"current_password": _IA_PASSWORD, "new_password": "anothernewpw1"},
    )
    assert resp.status_code == status.HTTP_204_NO_CONTENT


async def test_root_appears_in_db_list(client: AsyncClient, bootstrap_root: dict) -> None:
    """root 是真實列 → 出現在 admins 列表（與舊哨兵模型相反）。"""
    token = (await _login(client))["access_token"]
    listed = await client.get("/admin/admins?status=all", headers=_auth(token))
    usernames = {i["username"] for i in listed.json()["items"]}
    assert _IA_USERNAME in usernames


async def test_cannot_create_db_admin_with_root_username(
    client: AsyncClient, bootstrap_root: dict
) -> None:
    """保留字：不可用 bootstrap root 的 username 另建 DB admin → 409。"""
    token = (await _login(client))["access_token"]
    resp = await client.post(
        "/admin/admins",
        headers=_auth(token),
        json={"username": _IA_USERNAME, "name": "Clash", "password": "longpassword"},
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
