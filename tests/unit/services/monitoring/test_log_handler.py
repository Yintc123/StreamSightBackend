"""RedisStreamLogHandler：非阻塞 emit + 背景 flush（monitoring.md §2.3/§7.2）。"""

import asyncio
import logging

import fakeredis.aioredis
import pytest

from app.services.monitoring.log_handler import RedisStreamLogHandler, run_log_flusher
from app.services.monitoring.store import RedisStreamStore


def _make_record(msg: str, level: int = logging.INFO, name: str = "test") -> logging.LogRecord:
    record = logging.LogRecord(
        name=name, level=level, pathname="", lineno=0, msg=msg, args=(), exc_info=None
    )
    record.request_id = "req-test"  # type: ignore[attr-defined]
    return record


def test_emit_puts_to_queue() -> None:
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10)
    handler = RedisStreamLogHandler(queue)

    handler.emit(_make_record("hello"))

    assert queue.qsize() == 1
    item = queue.get_nowait()
    assert item["level"] == "INFO"
    assert item["message"] == "hello"


def test_emit_full_queue_drops_silently() -> None:
    """佇列滿 → 丟棄最舊、不拋、不阻塞（monitoring.md §2.3 best-effort）。"""
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=2)
    handler = RedisStreamLogHandler(queue)

    handler.emit(_make_record("a"))
    handler.emit(_make_record("b"))
    # 第三條：佇列已滿，應靜默 drop
    handler.emit(_make_record("c"))

    assert queue.qsize() == 2  # 不超出上限、不拋


def test_emit_does_not_raise_on_any_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """emit 內部若有任何例外，不能往外拋（logging.Handler 合約）。"""
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10)
    handler = RedisStreamLogHandler(queue)

    # 讓 format 拋錯
    monkeypatch.setattr(handler, "format", lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    handler.emit(_make_record("x"))  # 不應拋


async def test_flusher_writes_to_store() -> None:
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    store = RedisStreamStore(fakeredis.aioredis.FakeRedis())

    queue.put_nowait({"ts": 1, "level": "INFO", "logger": "x", "message": "m"})
    queue.put_nowait({"ts": 2, "level": "ERROR", "logger": "y", "message": "e"})

    await run_log_flusher(
        queue,
        store,
        stream="monitor:stream:logs",
        maxlen=1000,
        batch_size=10,
        flush_once=True,
    )

    page = await store.query("monitor:stream:logs", limit=100)
    assert len(page.items) == 2


async def test_flusher_calls_append_many_once_per_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """N 筆佇列 → flush 只呼叫一次 append_many（pipeline N→1 round-trip；monitoring.md §2.3）。"""
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    store = RedisStreamStore(fakeredis.aioredis.FakeRedis())

    call_count = 0
    original = store.append_many

    async def _spy(*a: object, **kw: object) -> list[str]:
        nonlocal call_count
        call_count += 1
        return await original(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "append_many", _spy)

    for i in range(5):
        queue.put_nowait({"ts": i, "level": "INFO", "logger": "x", "message": f"m{i}"})

    await run_log_flusher(
        queue, store, stream="monitor:stream:logs", maxlen=1000, batch_size=10, flush_once=True
    )

    assert call_count == 1  # 5 筆 → 1 次 append_many，非 5 次 append


async def test_flusher_continues_on_store_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """flush 遇 Store 例外 → 整批 log warning，不中斷（all-or-nothing，best-effort 語意一致）。"""
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    store = RedisStreamStore(fakeredis.aioredis.FakeRedis())

    async def _bad_append_many(*a: object, **kw: object) -> list[str]:
        raise RuntimeError("redis down")

    monkeypatch.setattr(store, "append_many", _bad_append_many)

    queue.put_nowait({"ts": 1, "level": "INFO", "logger": "x", "message": "m"})

    # 不拋例外
    await run_log_flusher(
        queue,
        store,
        stream="monitor:stream:logs",
        maxlen=1000,
        batch_size=10,
        flush_once=True,
    )


async def test_flusher_passes_minid_when_retention_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """minid_seconds > 0 → append_many 收到非 None 的 minid（≈ now_ms - retention_ms）。"""
    import time

    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    store = RedisStreamStore(fakeredis.aioredis.FakeRedis())

    captured: dict = {}
    original = store.append_many

    async def _spy(*a: object, **kw: object) -> list[str]:
        captured["minid"] = kw.get("minid")
        return await original(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "append_many", _spy)

    queue.put_nowait({"ts": 1, "level": "INFO", "logger": "x", "message": "m"})
    retention_seconds = 3600

    await run_log_flusher(
        queue,
        store,
        stream="monitor:stream:logs",
        maxlen=1000,
        batch_size=10,
        minid_seconds=retention_seconds,
        flush_once=True,
    )

    assert captured.get("minid") is not None
    expected_approx = int(time.time() * 1000) - retention_seconds * 1000
    assert abs(captured["minid"] - expected_approx) < 5_000  # 5 秒容差


async def test_flusher_passes_no_minid_when_retention_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """minid_seconds=0（預設）→ append_many 收到 minid=None（只靠 MAXLEN）。"""
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    store = RedisStreamStore(fakeredis.aioredis.FakeRedis())

    captured: dict = {}
    original = store.append_many

    async def _spy(*a: object, **kw: object) -> list[str]:
        captured["minid"] = kw.get("minid")
        return await original(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "append_many", _spy)

    queue.put_nowait({"ts": 1, "level": "INFO", "logger": "x", "message": "m"})

    await run_log_flusher(
        queue,
        store,
        stream="monitor:stream:logs",
        maxlen=1000,
        batch_size=10,
        minid_seconds=0,
        flush_once=True,
    )

    assert captured.get("minid") is None
