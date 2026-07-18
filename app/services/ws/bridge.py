"""WsBridge — 各實例背景訂閱 Redis pub/sub → 投遞給本地連線 + 處理 kick（websocket §2.4/§2.5）。

於 app lifespan 啟動背景 task（psubscribe channel、投遞、處理 kick）；lifespan 關閉時優雅收斂。
"""

import asyncio
import contextlib
import json
import logging

import redis.asyncio as redis
from redis.asyncio.client import PubSub

from app.services.ws.manager import ConnectionManager
from app.services.ws.protocol import (
    CHANNEL_BROADCAST,
    PSUBSCRIBE_PATTERNS,
    WSCloseCode,
)

logger: logging.Logger = logging.getLogger(__name__)

_DISCONNECT_PRINCIPAL_PREFIX = "ws:disconnect:principal:"
_DISCONNECT_SID_PREFIX = "ws:disconnect:sid:"
_PRINCIPAL_PREFIX = "ws:principal:"
_TOPIC_PREFIX = "ws:topic:"


def _as_str(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


class WsBridge:
    """訂閱 Redis channel → 呼叫本實例 manager.send_local / broadcast_local / kick_local。"""

    def __init__(self, client: redis.Redis, manager: ConnectionManager) -> None:
        self._client: redis.Redis = client
        self._manager: ConnectionManager = manager
        self._pubsub: PubSub | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """psubscribe 所有 WS channel pattern，啟動背景投遞 task。"""
        self._pubsub = self._client.pubsub()
        await self._pubsub.psubscribe(*PSUBSCRIBE_PATTERNS)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """優雅收斂：取消 task、退訂、關閉 pubsub。"""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None

    async def _run(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                try:
                    await self._dispatch(_as_str(message["channel"]), message["data"])
                except Exception:
                    logger.exception("WS bridge dispatch error channel=%s", message.get("channel"))
        except asyncio.CancelledError:
            raise

    async def _dispatch(self, channel: str, raw: str | bytes) -> None:
        """依 channel 路由到本實例 manager。"""
        payload: dict = json.loads(_as_str(raw))

        if channel.startswith(_DISCONNECT_PRINCIPAL_PREFIX):
            principal_id = int(channel[len(_DISCONNECT_PRINCIPAL_PREFIX) :])
            await self._manager.kick_local(
                principal_id, code=payload.get("code", WSCloseCode.UNAUTHENTICATED)
            )
        elif channel.startswith(_DISCONNECT_SID_PREFIX):
            sid = channel[len(_DISCONNECT_SID_PREFIX) :]
            await self._manager.kick_local_sid(
                sid, code=payload.get("code", WSCloseCode.UNAUTHENTICATED)
            )
        elif channel.startswith(_PRINCIPAL_PREFIX):
            principal_id = int(channel[len(_PRINCIPAL_PREFIX) :])
            await self._manager.send_local(principal_id=principal_id, message=payload)
        elif channel.startswith(_TOPIC_PREFIX):
            topic = channel[len(_TOPIC_PREFIX) :]
            await self._manager.send_local(topic=topic, message=payload)
        elif channel == CHANNEL_BROADCAST:
            await self._manager.broadcast_local(payload)
