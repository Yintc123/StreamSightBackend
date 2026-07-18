"""WS 控制訊息迴圈：subscribe/unsubscribe/pong + topic 授權（websocket §3.2/§2.9/§7.2）。"""

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole
from app.services import AdminService
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _ticket(
    client: AsyncClient, username: str = ADMIN_USERNAME, password: str = ADMIN_PASSWORD
) -> str:
    login = await client.post(
        "/admin/auth/login", json={"username": username, "password": password}
    )
    access = login.json()["access_token"]
    resp = await client.post("/admin/ws/ticket", headers={"Authorization": f"Bearer {access}"})
    return resp.json()["ticket"]


def _url(ticket: str, cid: str = "tab-1") -> str:
    return f"http://test/admin/ws?ticket={ticket}&cid={cid}"


async def test_subscribe_returns_ack(ws_client: AsyncClient, admin) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(_url(ticket), ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws.send_json({"type": "subscribe", "topic": "monitor.jobs"})
        ack = await ws.receive_json()
    assert ack == {"type": "subscribed", "topic": "monitor.jobs"}


async def test_unsubscribe_returns_ack(ws_client: AsyncClient, admin) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(_url(ticket), ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws.send_json({"type": "subscribe", "topic": "t"})
        await ws.receive_json()  # subscribed
        await ws.send_json({"type": "unsubscribe", "topic": "t"})
        ack = await ws.receive_json()
    assert ack == {"type": "unsubscribed", "topic": "t"}


async def test_unknown_type_returns_error_without_closing(ws_client: AsyncClient, admin) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(_url(ticket), ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws.send_json({"type": "not-a-real-type"})
        err = await ws.receive_json()
        assert err["type"] == "error"
        # 連線續存：仍可正常訂閱
        await ws.send_json({"type": "subscribe", "topic": "still-alive"})
        ack = await ws.receive_json()
    assert ack == {"type": "subscribed", "topic": "still-alive"}


async def test_pong_keeps_connection_alive(ws_client: AsyncClient, admin) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(_url(ticket), ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws.send_json({"type": "pong"})
        # pong 無回應；連線續存 → 後續 subscribe 仍得 ack
        await ws.send_json({"type": "subscribe", "topic": "t"})
        ack = await ws.receive_json()
    assert ack == {"type": "subscribed", "topic": "t"}


async def test_subscribe_forbidden_topic_returns_error(
    ws_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """越權 topic（viewer 訂 super_admin-only）→ error、連線續存（不關閉整條）。"""
    from app.services.ws import topics

    monkeypatch.setitem(topics.TOPIC_MIN_ROLE, "monitor.secrets", AdminRole.SUPER_ADMIN)
    await AdminService(db_session).create(
        username="ws-viewer", name="V", password="longpassword", admin_role=AdminRole.VIEWER
    )
    ticket = await _ticket(ws_client, username="ws-viewer", password="longpassword")

    async with aconnect_ws(_url(ticket), ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws.send_json({"type": "subscribe", "topic": "monitor.secrets"})
        err = await ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "forbidden_topic"
        # 連線續存：一般 topic 仍可訂
        await ws.send_json({"type": "subscribe", "topic": "monitor.public"})
        ack = await ws.receive_json()
    assert ack == {"type": "subscribed", "topic": "monitor.public"}
