"""LogQueryService：篩選 + 游標分頁（monitoring.md §2.7/§7.3）。"""

import fakeredis.aioredis
import pytest

from app.services.monitoring.logs import LogQueryService
from app.services.monitoring.store import RedisStreamStore

STREAM = "monitor:stream:logs"


@pytest.fixture
def store() -> RedisStreamStore:
    return RedisStreamStore(fakeredis.aioredis.FakeRedis())


@pytest.fixture
def svc(store: RedisStreamStore) -> LogQueryService:
    return LogQueryService(store)


async def _seed(store: RedisStreamStore, entries: list[dict]) -> list[str]:
    ids = []
    for entry in entries:
        eid = await store.append(STREAM, entry, maxlen=10000)
        ids.append(eid)
    return ids


async def test_query_returns_all_entries(svc: LogQueryService, store: RedisStreamStore) -> None:
    await _seed(
        store,
        [
            {"ts": "1", "level": "INFO", "logger": "a", "message": "m1"},
            {"ts": "2", "level": "ERROR", "logger": "b", "message": "m2"},
        ],
    )
    page = await svc.query(limit=100)
    assert len(page.items) == 2


async def test_query_filter_by_level(svc: LogQueryService, store: RedisStreamStore) -> None:
    await _seed(
        store,
        [
            {"ts": "1", "level": "INFO", "logger": "a", "message": "ok"},
            {"ts": "2", "level": "ERROR", "logger": "b", "message": "bad"},
            {"ts": "3", "level": "INFO", "logger": "c", "message": "also ok"},
        ],
    )
    page = await svc.query(level="ERROR", limit=100)
    assert len(page.items) == 1
    assert page.items[0].level == "ERROR"


async def test_query_filter_by_request_id(svc: LogQueryService, store: RedisStreamStore) -> None:
    await _seed(
        store,
        [
            {"ts": "1", "level": "INFO", "logger": "a", "message": "m", "request_id": "req-abc"},
            {"ts": "2", "level": "INFO", "logger": "b", "message": "m", "request_id": "req-xyz"},
        ],
    )
    page = await svc.query(request_id="req-abc", limit=100)
    assert len(page.items) == 1
    assert page.items[0].request_id == "req-abc"


async def test_query_filter_by_logger(svc: LogQueryService, store: RedisStreamStore) -> None:
    await _seed(
        store,
        [
            {"ts": "1", "level": "INFO", "logger": "app.auth", "message": "a"},
            {"ts": "2", "level": "INFO", "logger": "app.health", "message": "b"},
        ],
    )
    page = await svc.query(logger="app.auth", limit=100)
    assert len(page.items) == 1
    assert page.items[0].logger == "app.auth"


async def test_query_limit_clamped_to_max(svc: LogQueryService, store: RedisStreamStore) -> None:
    await _seed(
        store, [{"ts": str(i), "level": "INFO", "logger": "x", "message": "m"} for i in range(5)]
    )
    page = await svc.query(limit=2)
    assert len(page.items) == 2
    assert page.next_cursor is not None


async def test_query_empty_returns_no_cursor(svc: LogQueryService, store: RedisStreamStore) -> None:
    page = await svc.query(limit=100)
    assert page.items == []
    assert page.next_cursor is None
