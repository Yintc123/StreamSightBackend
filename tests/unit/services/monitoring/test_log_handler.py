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


async def test_flusher_continues_on_store_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """flush 遇 Store 例外 → 只 log warning，佇列清掉，不中斷（best-effort）。"""
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    store = RedisStreamStore(fakeredis.aioredis.FakeRedis())

    async def _bad_append(*a: object, **kw: object) -> str:
        raise RuntimeError("redis down")

    monkeypatch.setattr(store, "append", _bad_append)

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
