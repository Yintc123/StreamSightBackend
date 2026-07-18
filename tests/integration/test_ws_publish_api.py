"""端到端推播：Publisher → Redis pub/sub → bridge → WS client 收到 event（websocket §7.2）。

ws_client fixture 已於同一 fake_redis 起一個 bridge，故 Publisher 發佈能投遞到真實 WS 連線。
"""

import redis.asyncio as redis
from httpx import AsyncClient
from httpx_ws import aconnect_ws

from app.services.ws.publisher import Publisher
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _ticket(client: AsyncClient) -> str:
    login = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    access = login.json()["access_token"]
    resp = await client.post("/admin/ws/ticket", headers={"Authorization": f"Bearer {access}"})
    return resp.json()["ticket"]


async def test_subscribe_then_receive_topic_event(
    ws_client: AsyncClient, admin, fake_redis: redis.Redis
) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(f"http://test/admin/ws?ticket={ticket}&cid=c1", ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws.send_json({"type": "subscribe", "topic": "monitor.jobs"})
        assert (await ws.receive_json())["type"] == "subscribed"

        await Publisher(fake_redis).to_topic(
            "monitor.jobs", {"type": "event", "topic": "monitor.jobs", "ts": 1, "data": {"n": 1}}
        )
        event = await ws.receive_json()

    assert event["type"] == "event"
    assert event["topic"] == "monitor.jobs"
    assert event["data"] == {"n": 1}


async def test_unsubscribe_stops_receiving(
    ws_client: AsyncClient, admin, fake_redis: redis.Redis
) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(f"http://test/admin/ws?ticket={ticket}&cid=c1", ws_client) as ws:
        await ws.receive_json()  # welcome
        await ws.send_json({"type": "subscribe", "topic": "t"})
        await ws.receive_json()  # subscribed
        await ws.send_json({"type": "unsubscribe", "topic": "t"})
        await ws.receive_json()  # unsubscribed

        # 退訂後推播不應送達；改用 to_principal 送一則「哨兵」確認順序（先發 topic、再發 principal）
        await Publisher(fake_redis).to_topic(
            "t", {"type": "event", "topic": "t", "ts": 1, "data": {}}
        )
        await Publisher(fake_redis).to_principal(
            admin.principal_id, {"type": "event", "topic": "__sentinel__", "ts": 2, "data": {}}
        )
        received = await ws.receive_json()

    # 若退訂生效，第一則收到的必是哨兵（topic 那則被丟棄）
    assert received["topic"] == "__sentinel__"
