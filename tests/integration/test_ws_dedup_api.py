"""同分頁取代 / 兄弟分頁並存（websocket §2.12b/§7.7）。

同一登入（同 sid）＋同 cid → 後連取代前連（close 4409）；同 sid、不同 cid → 並存。
"""

from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _access(client: AsyncClient) -> str:
    login = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    return login.json()["access_token"]


async def _ticket(client: AsyncClient, access: str) -> str:
    resp = await client.post("/ws/ticket", headers={"Authorization": f"Bearer {access}"})
    return resp.json()["ticket"]


def _url(ticket: str, cid: str) -> str:
    return f"http://test/ws?ticket={ticket}&cid={cid}"


async def _read_until_close(ws) -> int:
    try:
        while True:
            await ws.receive_json()
    except WebSocketDisconnect as e:
        return e.code


async def test_same_tab_second_connection_replaces_first_4409(
    ws_client: AsyncClient, admin
) -> None:
    access = await _access(ws_client)  # 同一登入 → 兩張 ticket 同 sid
    t1 = await _ticket(ws_client, access)
    t2 = await _ticket(ws_client, access)

    async with aconnect_ws(_url(t1, "tab-1"), ws_client) as ws1:
        await ws1.receive_json()  # welcome
        async with aconnect_ws(_url(t2, "tab-1"), ws_client) as ws2:
            await ws2.receive_json()  # 新連線 welcome
            # 同 (sid, cid) → 舊連線被 close 4409
            assert await _read_until_close(ws1) == 4409


async def test_sibling_tabs_same_sid_coexist(ws_client: AsyncClient, admin) -> None:
    access = await _access(ws_client)
    t1 = await _ticket(ws_client, access)
    t2 = await _ticket(ws_client, access)

    async with (
        aconnect_ws(_url(t1, "tab-A"), ws_client) as ws_a,
        aconnect_ws(_url(t2, "tab-B"), ws_client) as ws_b,
    ):
        await ws_a.receive_json()  # welcome
        await ws_b.receive_json()  # welcome
        # 兩個分頁並存：各自 subscribe 都能拿到 ack（未互踢）
        await ws_a.send_json({"type": "subscribe", "topic": "t"})
        await ws_b.send_json({"type": "subscribe", "topic": "t"})
        assert (await ws_a.receive_json())["type"] == "subscribed"
        assert (await ws_b.receive_json())["type"] == "subscribed"
