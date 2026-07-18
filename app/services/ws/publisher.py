"""Publisher — 對 principal/topic/broadcast 推播與 kick（websocket §2.4/§2.5）。

發佈到 Redis channel；各實例 bridge 訂閱後投遞給本實例符合的連線。Redis pub/sub 為
at-most-once、無持久化（實例當下沒有對應連線就丟棄）——符合本期即時 best-effort 定位。
供各業務 service 注入使用。
"""

import json

import redis.asyncio as redis

from app.services.ws.protocol import (
    CHANNEL_BROADCAST,
    WSCloseCode,
    channel_disconnect_principal,
    channel_disconnect_sid,
    channel_principal,
    channel_topic,
)


class Publisher:
    """把「推給 principal / topic / 全體」或「kick」發佈到 Redis，跨實例 fan-out。"""

    def __init__(self, client: redis.Redis) -> None:
        self._client: redis.Redis = client

    async def to_principal(self, principal_id: int, message: dict) -> None:
        await self._client.publish(channel_principal(principal_id), json.dumps(message))

    async def to_topic(self, topic: str, message: dict) -> None:
        await self._client.publish(channel_topic(topic), json.dumps(message))

    async def broadcast(self, message: dict) -> None:
        await self._client.publish(CHANNEL_BROADCAST, json.dumps(message))

    async def disconnect_principal(
        self, principal_id: int, code: int = WSCloseCode.UNAUTHENTICATED
    ) -> None:
        """kick 該 principal 的全部 WS（archive/delete/change_password/logout_all，§2.5）。"""
        await self._client.publish(
            channel_disconnect_principal(principal_id), json.dumps({"code": code})
        )

    async def disconnect_session(
        self, family_id: str, code: int = WSCloseCode.UNAUTHENTICATED
    ) -> None:
        """僅 kick 該 session（sid）的 WS（單一 logout，§2.5）。"""
        await self._client.publish(channel_disconnect_sid(family_id), json.dumps({"code": code}))
