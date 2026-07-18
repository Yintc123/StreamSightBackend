"""撤權／登出即時斷線（websocket §2.5/§7.5）。

reauth 維持預設 300s（不縮短）→ 測試在數毫秒內通過即證明是 **kick** 路徑關閉，非複查兜底。
最後一個測試相反：不送 kick、縮短 reauth → 驗證兜底複查（session 無 live token）也能斷。
"""

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_app_settings
from app.core.enums import AdminRole
from app.services import AdminService
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _login(
    client: AsyncClient, username: str = ADMIN_USERNAME, password: str = ADMIN_PASSWORD
) -> dict:
    resp = await client.post("/admin/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()


async def _ticket(client: AsyncClient, access: str) -> str:
    resp = await client.post("/admin/ws/ticket", headers={"Authorization": f"Bearer {access}"})
    return resp.json()["ticket"]


async def _read_until_close(ws) -> int:
    try:
        while True:
            await ws.receive_json()
    except WebSocketDisconnect as e:
        return e.code


async def test_single_logout_kicks_only_that_session(ws_client: AsyncClient, admin) -> None:
    tokens = await _login(ws_client)
    ticket = await _ticket(ws_client, tokens["access_token"])
    async with aconnect_ws(f"http://test/admin/ws?ticket={ticket}&cid=c1", ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws_client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
        assert await _read_until_close(ws) == 4401


async def test_logout_all_kicks_all_connections(ws_client: AsyncClient, admin) -> None:
    tokens = await _login(ws_client)
    t1 = await _ticket(ws_client, tokens["access_token"])
    t2 = await _ticket(ws_client, tokens["access_token"])
    async with (
        aconnect_ws(f"http://test/admin/ws?ticket={t1}&cid=c1", ws_client) as ws1,
        aconnect_ws(f"http://test/admin/ws?ticket={t2}&cid=c2", ws_client) as ws2,
    ):
        await ws1.receive_json()
        await ws2.receive_json()
        await ws_client.post(
            "/auth/logout-all", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert await _read_until_close(ws1) == 4401
        assert await _read_until_close(ws2) == 4401


async def test_archive_kicks_admin_ws(
    ws_client: AsyncClient, admin, db_session: AsyncSession
) -> None:
    editor = await AdminService(db_session).create(
        username="rev-editor", name="E", password="longpassword", admin_role=AdminRole.EDITOR
    )
    editor_tokens = await _login(ws_client, username="rev-editor", password="longpassword")
    ticket = await _ticket(ws_client, editor_tokens["access_token"])
    root = await _login(ws_client)

    async with aconnect_ws(f"http://test/admin/ws?ticket={ticket}&cid=c1", ws_client) as ws:
        await ws.receive_json()  # welcome
        resp = await ws_client.post(
            f"/admin/admins/{editor.id}/archive",
            headers={"Authorization": f"Bearer {root['access_token']}"},
        )
        assert resp.status_code == 200
        assert await _read_until_close(ws) == 4401


async def test_reauth_backstop_when_kick_missed(
    ws_client: AsyncClient, admin, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模擬 kick 漏掉：直接撤該 session 的 refresh（不發 kick）→ 複查週期內因 session 無 live token 斷。"""
    from datetime import UTC, datetime

    from app.core.auth import decode_token, extract_sid
    from app.repositories import RefreshTokenRepository

    monkeypatch.setattr(get_app_settings(), "ws_reauth_interval_seconds", 0.05)
    monkeypatch.setattr(get_app_settings(), "ws_ping_interval_seconds", 3600)

    tokens = await _login(ws_client)
    sid = extract_sid(decode_token(tokens["access_token"]))
    ticket = await _ticket(ws_client, tokens["access_token"])

    async with aconnect_ws(f"http://test/admin/ws?ticket={ticket}&cid=c1", ws_client) as ws:
        await ws.receive_json()  # welcome
        # 直接撤該 family（模擬登出但 kick 未送達）
        assert sid is not None
        await RefreshTokenRepository(db_session).revoke_family(sid, datetime.now(UTC))
        await db_session.commit()
        assert await _read_until_close(ws) == 4401
