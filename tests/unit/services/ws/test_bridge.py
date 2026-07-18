"""Publisher + bridge：Redis pub/sub 跨實例 fan-out + kick（websocket §2.4/§2.5/§7.4）。

以兩個 ConnectionManager 共用同一個 fake_redis 模擬兩實例。
"""

import asyncio

import redis.asyncio as redis

from app.services.ws.bridge import WsBridge
from app.services.ws.manager import ConnectionManager
from app.services.ws.protocol import WSCloseCode
from app.services.ws.publisher import Publisher
from tests.unit.services.ws.conftest import fake_ws, make_connection


async def _wait(cond, timeout: float = 1.0) -> None:
    """輪詢等待條件成立（pub/sub 非同步投遞，deterministic 上限等待）。"""
    for _ in range(int(timeout / 0.005)):
        if cond():
            return
        await asyncio.sleep(0.005)


async def test_to_topic_fans_out_across_instances(fake_redis: redis.Redis) -> None:
    mgr_a = ConnectionManager()
    mgr_b = ConnectionManager()
    bridge_a = WsBridge(fake_redis, mgr_a)
    bridge_b = WsBridge(fake_redis, mgr_b)
    await bridge_a.start()
    await bridge_b.start()

    a = make_connection(principal_id=1, sid="sA", cid="c")
    b = make_connection(principal_id=2, sid="sB", cid="c")
    await mgr_a.register(a)
    await mgr_b.register(b)
    await mgr_a.subscribe(a, "monitor.jobs")
    await mgr_b.subscribe(b, "monitor.jobs")

    await Publisher(fake_redis).to_topic("monitor.jobs", {"type": "event", "topic": "monitor.jobs"})

    await _wait(lambda: bool(fake_ws(a).sent) and bool(fake_ws(b).sent))
    await a.queue.join()
    await b.queue.join()
    assert fake_ws(a).sent[-1]["topic"] == "monitor.jobs"
    assert fake_ws(b).sent[-1]["topic"] == "monitor.jobs"

    await bridge_a.stop()
    await bridge_b.stop()
    await mgr_a._teardown(a)
    await mgr_b._teardown(b)


async def test_to_principal_delivers_to_all_its_connections(fake_redis: redis.Redis) -> None:
    mgr = ConnectionManager()
    bridge = WsBridge(fake_redis, mgr)
    await bridge.start()

    c1 = make_connection(principal_id=9, sid="s", cid="c1")
    c2 = make_connection(principal_id=9, sid="s", cid="c2")
    await mgr.register(c1)
    await mgr.register(c2)

    await Publisher(fake_redis).to_principal(9, {"type": "event"})

    await _wait(lambda: bool(fake_ws(c1).sent) and bool(fake_ws(c2).sent))
    assert fake_ws(c1).sent and fake_ws(c2).sent

    await bridge.stop()
    await mgr._teardown(c1)
    await mgr._teardown(c2)


async def test_disconnect_principal_kicks_all(fake_redis: redis.Redis) -> None:
    mgr = ConnectionManager()
    bridge = WsBridge(fake_redis, mgr)
    await bridge.start()

    c1 = make_connection(principal_id=3, sid="s", cid="c1")
    c2 = make_connection(principal_id=3, sid="s", cid="c2")
    await mgr.register(c1)
    await mgr.register(c2)

    await Publisher(fake_redis).disconnect_principal(3)

    await _wait(lambda: c1.closed and c2.closed)
    assert fake_ws(c1).closed_code == WSCloseCode.UNAUTHENTICATED
    assert fake_ws(c2).closed_code == WSCloseCode.UNAUTHENTICATED

    await bridge.stop()


async def test_disconnect_session_kicks_only_that_sid(fake_redis: redis.Redis) -> None:
    mgr = ConnectionManager()
    bridge = WsBridge(fake_redis, mgr)
    await bridge.start()

    s1 = make_connection(principal_id=3, sid="sess-1", cid="c1")
    s2 = make_connection(principal_id=3, sid="sess-2", cid="c2")
    await mgr.register(s1)
    await mgr.register(s2)

    await Publisher(fake_redis).disconnect_session("sess-1")

    await _wait(lambda: s1.closed)
    assert fake_ws(s1).closed_code == WSCloseCode.UNAUTHENTICATED
    assert s2.closed is False  # 其他 session 不受影響

    await bridge.stop()
    await mgr._teardown(s2)


async def test_broadcast_reaches_all(fake_redis: redis.Redis) -> None:
    mgr = ConnectionManager()
    bridge = WsBridge(fake_redis, mgr)
    await bridge.start()

    a = make_connection(principal_id=1, sid="s1", cid="c")
    b = make_connection(principal_id=2, sid="s2", cid="c")
    await mgr.register(a)
    await mgr.register(b)

    await Publisher(fake_redis).broadcast({"type": "event", "topic": "sys"})

    await _wait(lambda: bool(fake_ws(a).sent) and bool(fake_ws(b).sent))
    assert fake_ws(a).sent and fake_ws(b).sent

    await bridge.stop()
    await mgr._teardown(a)
    await mgr._teardown(b)
