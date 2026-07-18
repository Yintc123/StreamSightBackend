"""ConnectionManager：per-instance 註冊/投遞/清理（websocket §2.3/§2.8/§2.12）。"""

import asyncio

from app.services.ws.manager import ConnectionManager
from app.services.ws.protocol import WSCloseCode
from tests.unit.services.ws.conftest import FakeWebSocket, fake_ws, make_connection


async def _drain(conn) -> None:
    """等 writer 把佇列送完（deterministic：queue.join）。"""
    await conn.queue.join()


# ── 註冊 / 索引 ────────────────────────────────────────────────
async def test_register_indexes_by_principal_sid_cid() -> None:
    mgr = ConnectionManager()
    conn = make_connection(principal_id=5, sid="s1", cid="c1")
    await mgr.register(conn)

    assert conn in mgr.connections_for_principal(5)
    assert conn in mgr.connections_for_sid("s1")
    await mgr._teardown(conn)


async def test_unregister_clears_all_indices() -> None:
    mgr = ConnectionManager()
    conn = make_connection(principal_id=5, sid="s1", cid="c1")
    await mgr.register(conn)

    await mgr.unregister(conn)

    assert mgr.connections_for_principal(5) == set()
    assert mgr.connections_for_sid("s1") == set()


# ── 同分頁取代（(sid, cid)）§2.12b ─────────────────────────────
async def test_same_tab_replaces_old_with_4409() -> None:
    mgr = ConnectionManager()
    old_ws = FakeWebSocket()
    old = make_connection(principal_id=1, sid="s", cid="tab", ws=old_ws)
    new = make_connection(principal_id=1, sid="s", cid="tab")

    await mgr.register(old)
    await mgr.register(new)

    assert old_ws.closed_code == WSCloseCode.REPLACED  # 4409
    assert old not in mgr.connections_for_principal(1)
    assert new in mgr.connections_for_principal(1)
    await mgr._teardown(new)


async def test_sibling_tabs_coexist() -> None:
    """同 sid、不同 cid → 並存、互不影響（不誤殺兄弟分頁）。"""
    mgr = ConnectionManager()
    a = make_connection(principal_id=1, sid="s", cid="tabA")
    b = make_connection(principal_id=1, sid="s", cid="tabB")

    await mgr.register(a)
    await mgr.register(b)

    assert {a, b} <= mgr.connections_for_principal(1)
    assert fake_ws(a).closed_code is None and fake_ws(b).closed_code is None
    await mgr._teardown(a)
    await mgr._teardown(b)


# ── _teardown 冪等 §2.12a ──────────────────────────────────────
async def test_teardown_idempotent() -> None:
    mgr = ConnectionManager()
    conn = make_connection()
    await mgr.register(conn)

    await mgr._teardown(conn)
    await mgr._teardown(conn)  # 第二次 no-op、不爆

    assert conn.closed is True
    assert mgr.connections_for_principal(conn.principal_id) == set()


# ── subscribe / send_local ────────────────────────────────────
async def test_subscribe_then_send_local_by_topic() -> None:
    mgr = ConnectionManager()
    conn = make_connection()
    await mgr.register(conn)
    await mgr.subscribe(conn, "monitor.jobs")

    n = await mgr.send_local(
        topic="monitor.jobs", message={"type": "event", "topic": "monitor.jobs"}
    )
    await _drain(conn)

    assert n == 1
    assert fake_ws(conn).sent[-1]["topic"] == "monitor.jobs"
    await mgr._teardown(conn)


async def test_unsubscribe_stops_delivery() -> None:
    mgr = ConnectionManager()
    conn = make_connection()
    await mgr.register(conn)
    await mgr.subscribe(conn, "t")
    await mgr.unsubscribe(conn, "t")

    n = await mgr.send_local(topic="t", message={"type": "event"})
    assert n == 0
    await mgr._teardown(conn)


async def test_send_local_by_principal_hits_all_connections() -> None:
    mgr = ConnectionManager()
    a = make_connection(principal_id=9, sid="s", cid="c1")
    b = make_connection(principal_id=9, sid="s", cid="c2")
    await mgr.register(a)
    await mgr.register(b)

    n = await mgr.send_local(principal_id=9, message={"type": "event"})
    await _drain(a)
    await _drain(b)

    assert n == 2
    assert fake_ws(a).sent and fake_ws(b).sent
    await mgr._teardown(a)
    await mgr._teardown(b)


# ── 背壓：佇列滿 → close 1013 §2.8 ────────────────────────────
async def test_backpressure_full_queue_closes_1013() -> None:
    mgr = ConnectionManager()
    # 佇列容量 1、且不啟動 writer 消費 → 第二則塞爆
    conn = make_connection(max_queue=1)
    await mgr.register(conn, start_writer=False)

    await mgr.send_local(principal_id=conn.principal_id, message={"n": 1})
    await mgr.send_local(principal_id=conn.principal_id, message={"n": 2})

    assert fake_ws(conn).closed_code == WSCloseCode.BACKPRESSURE  # 1013
    assert conn.closed is True


# ── 送出失敗即斷 §2.12a ───────────────────────────────────────
async def test_send_failure_tears_down() -> None:
    from starlette.websockets import WebSocketDisconnect

    mgr = ConnectionManager()
    ws = FakeWebSocket()
    ws.raise_on_send = WebSocketDisconnect(code=1006)
    conn = make_connection(ws=ws)
    await mgr.register(conn)

    await mgr.send_local(principal_id=conn.principal_id, message={"type": "event"})
    # 等 writer 偵測到死亡並 teardown
    for _ in range(100):
        if conn.closed:
            break
        await asyncio.sleep(0.001)

    assert conn.closed is True
    assert mgr.connections_for_principal(conn.principal_id) == set()


async def test_serialization_typeerror_does_not_teardown() -> None:
    """壞 payload（TypeError）是 bug、只 log、不斷連線（不可 except Exception）。"""
    mgr = ConnectionManager()
    ws = FakeWebSocket()
    ws.raise_on_send = TypeError("not serializable")
    conn = make_connection(ws=ws)
    await mgr.register(conn)

    await mgr.send_local(principal_id=conn.principal_id, message={"bad": object()})
    await asyncio.sleep(0.01)

    assert conn.closed is False
    assert conn in mgr.connections_for_principal(conn.principal_id)
    await mgr._teardown(conn)


# ── kick_local §2.5 ───────────────────────────────────────────
async def test_kick_local_closes_principal_connections() -> None:
    mgr = ConnectionManager()
    a = make_connection(principal_id=3, sid="s", cid="c1")
    b = make_connection(principal_id=3, sid="s", cid="c2")
    await mgr.register(a)
    await mgr.register(b)

    await mgr.kick_local(3, code=WSCloseCode.UNAUTHENTICATED)

    assert fake_ws(a).closed_code == WSCloseCode.UNAUTHENTICATED
    assert fake_ws(b).closed_code == WSCloseCode.UNAUTHENTICATED
    assert mgr.connections_for_principal(3) == set()


# ── close_all：lifespan shutdown → 1012 §2.2/§3.4 ─────────────
async def test_close_all_closes_every_connection_with_service_restart() -> None:
    """實例關閉：對本實例全部連線送 close(1012) 並清乾淨（優雅斷線）。"""
    mgr = ConnectionManager()
    a = make_connection(principal_id=1, sid="s1", cid="c1")
    b = make_connection(principal_id=2, sid="s2", cid="c2")
    await mgr.register(a)
    await mgr.register(b)

    await mgr.close_all(WSCloseCode.SERVICE_RESTART)

    assert fake_ws(a).closed_code == WSCloseCode.SERVICE_RESTART  # 1012
    assert fake_ws(b).closed_code == WSCloseCode.SERVICE_RESTART
    assert a.closed is True and b.closed is True
    assert mgr.total_connections == 0


async def test_kick_local_by_sid_only_that_session() -> None:
    mgr = ConnectionManager()
    s1 = make_connection(principal_id=3, sid="sess-1", cid="c1")
    s2 = make_connection(principal_id=3, sid="sess-2", cid="c2")
    await mgr.register(s1)
    await mgr.register(s2)

    await mgr.kick_local_sid("sess-1", code=WSCloseCode.UNAUTHENTICATED)

    assert fake_ws(s1).closed_code == WSCloseCode.UNAUTHENTICATED
    assert fake_ws(s2).closed_code is None  # 其他 session 不受影響
    await mgr._teardown(s2)
