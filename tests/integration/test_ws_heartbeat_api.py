"""WS 心跳判死（close 4000）與定期複查失效（close 4401）（websocket §2.2/§2.7/§7.3）。

時間相關：不 sleep，把 ws_ping_interval_seconds／ws_missed_pong_limit／
ws_reauth_interval_seconds 以極小值 monkeypatch 覆寫（spike 陷阱 #3）。
"""

import asyncio

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_app_settings
from app.core.enums import AdminRole
from app.services import AdminService
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def _extract_close_code(exc: BaseException) -> int | None:
    if isinstance(exc, WebSocketDisconnect):
        return exc.code
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            code = _extract_close_code(sub)
            if code is not None:
                return code
    return None


async def _ticket(
    client: AsyncClient, username: str = ADMIN_USERNAME, password: str = ADMIN_PASSWORD
) -> str:
    login = await client.post(
        "/admin/auth/login", json={"username": username, "password": password}
    )
    access = login.json()["access_token"]
    resp = await client.post("/admin/ws/ticket", headers={"Authorization": f"Bearer {access}"})
    return resp.json()["ticket"]


async def _read_until_close(ws_client: AsyncClient, url: str) -> int:
    """讀 welcome 後持續讀，直到 server 關閉；回 close code（吞過程中的 ping）。"""
    try:
        async with aconnect_ws(url, ws_client) as ws:
            await ws.receive_json()  # welcome
            while True:
                await ws.receive_json()  # ping / 其他，直到斷線
    except WebSocketDisconnect as e:
        return e.code
    except BaseExceptionGroup as eg:
        code = _extract_close_code(eg)
        if code is None:
            raise
        return code
    raise AssertionError("expected the server to close the connection")


async def test_missed_pong_closes_4000(
    ws_client: AsyncClient, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_app_settings()
    monkeypatch.setattr(settings, "ws_ping_interval_seconds", 0.05)
    monkeypatch.setattr(settings, "ws_missed_pong_limit", 1)
    monkeypatch.setattr(settings, "ws_reauth_interval_seconds", 3600)  # 別讓複查插手

    ticket = await _ticket(ws_client)
    code = await _read_until_close(ws_client, f"http://test/admin/ws?ticket={ticket}&cid=c1")
    assert code == 4000


async def test_idle_timeout_closes_4000(
    ws_client: AsyncClient, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """連線無任何進站訊息（含 pong）超過 idle_timeout → close 4000（§2.7）。

    以高 missed_pong_limit 隔離出 idle 路徑（避免心跳 missed-pong 先觸發 4000）。
    """
    settings = get_app_settings()
    monkeypatch.setattr(settings, "ws_idle_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "ws_ping_interval_seconds", 0.02)
    monkeypatch.setattr(settings, "ws_missed_pong_limit", 100000)
    monkeypatch.setattr(settings, "ws_reauth_interval_seconds", 3600)

    ticket = await _ticket(ws_client)
    code = await _read_until_close(ws_client, f"http://test/admin/ws?ticket={ticket}&cid=c1")
    assert code == 4000


async def test_activity_resets_idle_timer(
    ws_client: AsyncClient, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有進站訊息（pong）→ 重置 idle 計時，連線不因閒置被斷（送 pong 後仍可訂閱）。"""
    settings = get_app_settings()
    monkeypatch.setattr(settings, "ws_idle_timeout_seconds", 0.2)
    monkeypatch.setattr(settings, "ws_ping_interval_seconds", 0.02)
    monkeypatch.setattr(settings, "ws_missed_pong_limit", 100000)
    monkeypatch.setattr(settings, "ws_reauth_interval_seconds", 3600)

    ticket = await _ticket(ws_client)
    async with aconnect_ws(f"http://test/admin/ws?ticket={ticket}&cid=c1", ws_client) as ws:
        await ws.receive_json()  # welcome
        # 持續送 pong 保活（跨越數個 idle 窗）
        for _ in range(6):
            await ws.send_json({"type": "pong"})
            await asyncio.sleep(0.05)
        await ws.send_json({"type": "subscribe", "topic": "t"})
        # 讀掉可能已排入的 ping，直到拿到 subscribed
        got_subscribed = False
        for _ in range(50):
            msg = await ws.receive_json()
            if msg["type"] == "subscribed":
                got_subscribed = True
                break
        assert got_subscribed


async def test_reauth_archived_closes_4401(
    ws_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """連線中的 admin 被封存 → 複查週期內 close 4401（無硬性連線上限，複查兜底）。"""
    settings = get_app_settings()
    monkeypatch.setattr(settings, "ws_reauth_interval_seconds", 0.05)
    monkeypatch.setattr(settings, "ws_ping_interval_seconds", 3600)  # 別讓心跳插手

    editor = await AdminService(db_session).create(
        username="hb-editor", name="E", password="longpassword", admin_role=AdminRole.EDITOR
    )
    ticket = await _ticket(ws_client, username="hb-editor", password="longpassword")

    async with aconnect_ws(f"http://test/admin/ws?ticket={ticket}&cid=c1", ws_client) as ws:
        await ws.receive_json()  # welcome
        await AdminService(db_session).archive(editor.id)
        try:
            while True:
                await ws.receive_json()
        except WebSocketDisconnect as e:
            assert e.code == 4401
            return
    raise AssertionError("expected close 4401")
