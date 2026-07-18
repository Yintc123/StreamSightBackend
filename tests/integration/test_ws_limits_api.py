"""資源上限 / 協定 / Origin（websocket §6/§7.6）。

- 超過 per-principal 連線數 → close 1013。
- 超大訊息 / 非 JSON → close 4400。
- 控制訊息速率超限 → error（不斷線）。
- 錯 Origin → handshake 拒絕（未 accept）。
"""

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, WebSocketUpgradeError, aconnect_ws

from app.core.config import get_app_settings
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _ticket(client: AsyncClient) -> str:
    login = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    access = login.json()["access_token"]
    resp = await client.post("/admin/ws/ticket", headers={"Authorization": f"Bearer {access}"})
    return resp.json()["ticket"]


def _url(ticket: str, cid: str) -> str:
    return f"http://test/admin/ws?ticket={ticket}&cid={cid}"


def _extract_close_code(exc: BaseException) -> int | None:
    """挖出 close code（斷線例外可能被 anyio 包成 ExceptionGroup，spike 陷阱 #1）。"""
    if isinstance(exc, WebSocketDisconnect):
        return exc.code
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            code = _extract_close_code(sub)
            if code is not None:
                return code
    return None


async def test_per_principal_limit_rejects_with_1013(
    ws_client: AsyncClient, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_app_settings(), "ws_max_connections_per_principal", 2)
    t1 = await _ticket(ws_client)
    t2 = await _ticket(ws_client)
    t3 = await _ticket(ws_client)

    async with (
        aconnect_ws(_url(t1, "c1"), ws_client) as ws1,
        aconnect_ws(_url(t2, "c2"), ws_client) as ws2,
    ):
        await ws1.receive_json()
        await ws2.receive_json()
        # 第 3 條（不同 cid）超過 per-principal 上限 → close 1013
        try:
            async with aconnect_ws(_url(t3, "c3"), ws_client) as ws3:
                await ws3.receive_json()
            raise AssertionError("expected 3rd connection to be rejected")
        except (WebSocketDisconnect, BaseExceptionGroup) as e:
            assert _extract_close_code(e) == 1013


async def test_oversize_message_closes_4400(ws_client: AsyncClient, admin) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(_url(ticket, "c1"), ws_client) as ws:
        await ws.receive_json()  # welcome
        big = "x" * (get_app_settings().ws_max_message_bytes + 1)
        await ws.send_json({"type": "subscribe", "topic": big})
        try:
            while True:
                await ws.receive_json()
        except WebSocketDisconnect as e:
            assert e.code == 4400


async def test_non_json_closes_4400(ws_client: AsyncClient, admin) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(_url(ticket, "c1"), ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws.send_text("this is not json{{{")
        try:
            while True:
                await ws.receive_json()
        except WebSocketDisconnect as e:
            assert e.code == 4400


async def test_rate_limit_returns_error_without_closing(
    ws_client: AsyncClient, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_app_settings(), "ws_control_msg_rate_limit", 3)
    ticket = await _ticket(ws_client)
    async with aconnect_ws(_url(ticket, "c1"), ws_client) as ws:
        await ws.receive_json()  # welcome
        for i in range(5):
            await ws.send_json({"type": "subscribe", "topic": f"t{i}"})
        results = [await ws.receive_json() for _ in range(5)]

    types = [r["type"] for r in results]
    assert types[:3] == ["subscribed", "subscribed", "subscribed"]
    assert all(t == "error" for t in types[3:])
    assert results[3]["code"] == "rate_limited"


async def test_wrong_origin_rejected_at_handshake(
    ws_client: AsyncClient, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_app_settings(), "ws_allowed_origins", ["https://good.example"])
    ticket = await _ticket(ws_client)
    with pytest.raises((WebSocketUpgradeError, WebSocketDisconnect)):
        async with aconnect_ws(
            _url(ticket, "c1"), ws_client, headers={"origin": "https://evil.example"}
        ) as ws:
            await ws.receive_json()


async def test_allowed_origin_accepts(
    ws_client: AsyncClient, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_app_settings(), "ws_allowed_origins", ["https://good.example"])
    ticket = await _ticket(ws_client)
    async with aconnect_ws(
        _url(ticket, "c1"), ws_client, headers={"origin": "https://good.example"}
    ) as ws:
        welcome = await ws.receive_json()
    assert welcome["type"] == "welcome"
