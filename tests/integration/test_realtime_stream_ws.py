"""Integration test for WS delivery of realtime.stream data（realtime-stream.md §6.3 test 8）。

TDD: RED → GREEN → REFACTOR。

ws_client fixture（ASGIWebSocketTransport + bridge on fake_redis）不跑 lifespan，
故手動建立 RealtimeStreamer 並注入同一個 fake_redis，讓 Publisher→Redis→bridge→WS 全鏈路可測。
"""

import asyncio

import redis.asyncio as redis
from httpx import AsyncClient
from httpx_ws import aconnect_ws

from app.services.realtime.streamer import RealtimeStreamer
from app.services.ws.publisher import Publisher
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _ticket(client: AsyncClient) -> str:
    login = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    access = login.json()["access_token"]
    resp = await client.post("/ws/ticket", headers={"Authorization": f"Bearer {access}"})
    return resp.json()["ticket"]


# ── test 8：WS 訊息投遞 ────────────────────────────────────────────────────────


async def test_streamer_delivers_to_ws(
    ws_client: AsyncClient, admin, fake_redis: redis.Redis
) -> None:
    """RealtimeStreamer tick 透過 Publisher → Redis pub/sub → bridge → WS client 收到 data 訊息
    （realtime-stream.md §6.3 test 8）。

    訊息格式：{"type":"data","topic":"realtime.stream","value":<float>,"ts":<ISO8601 UTC>}
    """
    ticket = await _ticket(ws_client)

    # 手動建立 streamer，注入與 ws_client fixture 同一個 fake_redis（共用 bridge）
    streamer = RealtimeStreamer(
        publisher=Publisher(fake_redis),
        redis_client=fake_redis,
    )

    async with aconnect_ws(f"http://test/ws?ticket={ticket}&cid=c1", ws_client) as ws:
        await ws.receive_json()  # welcome

        await ws.send_json({"type": "subscribe", "topic": "realtime.stream"})
        subscribed = await ws.receive_json()
        assert subscribed["type"] == "subscribed"

        # streamer 啟動後等待第一個 tick（sleep 1s）
        await streamer.start()
        event = await asyncio.wait_for(ws.receive_json(), timeout=2.5)

    await streamer.stop()

    assert event["type"] == "data"
    assert event["topic"] == "realtime.stream"
    assert isinstance(event["value"], float)
    assert 0.0 <= event["value"] <= 100.0
    ts_str: str = event["ts"]
    assert isinstance(ts_str, str)
    assert "+00:00" in ts_str or ts_str.endswith("Z"), f"ts 必須含 UTC 後綴，得到 {ts_str!r}"
