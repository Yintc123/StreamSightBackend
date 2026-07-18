"""TicketService：短命、單次、Redis-backed ticket（websocket §2.1）。

issue → SET ws:ticket:{t} = {principal_id, sid} EX ttl；
consume → 原子 GETDEL（保證單次、防重放）。
"""

import redis.asyncio as redis

from app.core.config import get_app_settings
from app.services.ws.ticket import TicketService


async def test_issue_returns_ticket_and_ttl(fake_redis: redis.Redis) -> None:
    svc = TicketService(fake_redis)
    ticket, ttl = await svc.issue(principal_id=7, sid="fam-1")

    assert isinstance(ticket, str) and len(ticket) >= 32
    assert ttl == get_app_settings().ws_ticket_ttl_seconds


async def test_issue_stores_with_expiry(fake_redis: redis.Redis) -> None:
    svc = TicketService(fake_redis)
    ticket, ttl = await svc.issue(principal_id=7, sid="fam-1")

    remaining = await fake_redis.ttl(f"ws:ticket:{ticket}")
    assert 0 < remaining <= ttl


async def test_consume_returns_principal_and_sid(fake_redis: redis.Redis) -> None:
    svc = TicketService(fake_redis)
    ticket, _ = await svc.issue(principal_id=42, sid="fam-9")

    result = await svc.consume(ticket)

    assert result == (42, "fam-9")


async def test_consume_is_single_use(fake_redis: redis.Redis) -> None:
    """原子 GETDEL：同一 ticket 第二次消費 → None（防重放）。"""
    svc = TicketService(fake_redis)
    ticket, _ = await svc.issue(principal_id=1, sid="fam-1")

    assert await svc.consume(ticket) == (1, "fam-1")
    assert await svc.consume(ticket) is None


async def test_consume_unknown_ticket_returns_none(fake_redis: redis.Redis) -> None:
    svc = TicketService(fake_redis)
    assert await svc.consume("never-issued") is None


async def test_issue_sid_none_roundtrips(fake_redis: redis.Redis) -> None:
    """初始 admin（無 refresh session）→ sid None，可正常簽發／消費。"""
    svc = TicketService(fake_redis)
    ticket, _ = await svc.issue(principal_id=0, sid=None)

    assert await svc.consume(ticket) == (0, None)
