"""TicketService — 兩段式 WS 認證的第一段（websocket §2.1）。

長命 access token 只在 POST /admin/ws/ticket 的 HTTP header；WS URL 只帶
一張短命（預設 180s）、單次、opaque 的 ticket。即使 ticket 洩漏在 URL/log，
原子 GETDEL 保證第一次消費即失效 → 無法重放。
"""

import json
import secrets

import redis.asyncio as redis

from app.core.config import get_app_settings

_TICKET_PREFIX = "ws:ticket:"


class TicketService:
    """簽發（issue）+ 原子單次消費（consume）Redis-backed WS ticket。"""

    def __init__(self, client: redis.Redis) -> None:
        self._client: redis.Redis = client

    def _key(self, ticket: str) -> str:
        return f"{_TICKET_PREFIX}{ticket}"

    async def issue(self, principal_id: int, sid: str | None) -> tuple[str, int]:
        """產 opaque ticket、SET ws:ticket:{t} = {principal_id, sid} EX ttl；回 (ticket, ttl)。

        ttl = ws_ticket_ttl_seconds（換票→開連線的寬限窗，非連線時長）。
        """
        ttl: int = get_app_settings().ws_ticket_ttl_seconds
        ticket: str = secrets.token_urlsafe(32)
        payload: str = json.dumps({"principal_id": principal_id, "sid": sid})
        await self._client.set(self._key(ticket), payload, ex=ttl)
        return ticket, ttl

    async def consume(self, ticket: str) -> tuple[int, str | None] | None:
        """原子 GETDEL ws:ticket:{t}；回 (principal_id, sid) 或 None（不存在/已用過/過期）。

        單次：同一 ticket 第二次消費 → 查無 → None（防重放）。
        """
        raw: str | bytes | None = await self._client.getdel(self._key(ticket))
        if raw is None:
            return None
        data: dict = json.loads(raw)
        return int(data["principal_id"]), data.get("sid")
