"""RedisStreamStore：append / query 游標分頁（monitoring.md §2.2/§7.1）。"""

import fakeredis.aioredis
import pytest

from app.dtos.monitoring import Page
from app.services.monitoring.store import RedisStreamStore


@pytest.fixture
def store() -> RedisStreamStore:
    return RedisStreamStore(fakeredis.aioredis.FakeRedis())


async def test_append_returns_entry_id(store: RedisStreamStore) -> None:
    eid = await store.append("test:stream", {"level": "INFO", "msg": "hello"})
    assert isinstance(eid, str)
    assert "-" in eid


async def test_append_maxlen_trims_oldest(store: RedisStreamStore) -> None:
    for i in range(5):
        await store.append("s", {"i": str(i)}, maxlen=3)
    page = await store.query("s", limit=100)
    assert len(page.items) == 3


async def test_query_empty_stream_returns_empty_page(store: RedisStreamStore) -> None:
    page = await store.query("nonexistent:stream", limit=10)
    assert page.items == []
    assert page.next_cursor is None


async def test_query_returns_all_within_range(store: RedisStreamStore) -> None:
    for i in range(5):
        await store.append("s2", {"n": str(i)})
    page = await store.query("s2", limit=100)
    assert len(page.items) == 5


async def test_query_limit_and_cursor_pagination(store: RedisStreamStore) -> None:
    for i in range(10):
        await store.append("s3", {"n": str(i)})

    page1 = await store.query("s3", limit=4)
    assert len(page1.items) == 4
    assert page1.next_cursor is not None

    page2 = await store.query("s3", cursor=page1.next_cursor, limit=4)
    assert len(page2.items) == 4
    assert page2.next_cursor is not None

    page3 = await store.query("s3", cursor=page2.next_cursor, limit=4)
    assert len(page3.items) == 2
    assert page3.next_cursor is None


async def test_query_since_until_filters_by_id_range(store: RedisStreamStore) -> None:
    id1 = await store.append("s4", {"n": "1"})
    id2 = await store.append("s4", {"n": "2"})
    await store.append("s4", {"n": "3"})

    ms1 = int(id1.split("-")[0])
    ms2 = int(id2.split("-")[0])
    page = await store.query("s4", since=ms1, until=ms2, limit=100)
    assert len(page.items) >= 1
    assert all(int(item["_id"].split("-")[0]) <= ms2 for item in page.items)


async def test_append_many_writes_n_entries_and_returns_ids(store: RedisStreamStore) -> None:
    """append_many 一次寫入 N 筆，回 N 個 id（monitoring.md §2.2）。"""
    entries = [{"n": str(i)} for i in range(3)]
    ids = await store.append_many("s_many", entries)
    assert len(ids) == 3
    assert all("-" in eid for eid in ids)
    page = await store.query("s_many", limit=100)
    assert len(page.items) == 3


async def test_append_many_empty_returns_empty(store: RedisStreamStore) -> None:
    """append_many 空 list → 回空 list，不寫入。"""
    ids = await store.append_many("s_empty", [])
    assert ids == []


async def test_append_many_maxlen_trims_oldest(store: RedisStreamStore) -> None:
    """append_many 帶 maxlen → 舊資料被修剪（pipeline XADD MAXLEN ~）。"""
    entries = [{"n": str(i)} for i in range(5)]
    await store.append_many("s_trim", entries, maxlen=3)
    page = await store.query("s_trim", limit=100)
    assert len(page.items) == 3


async def test_append_accepts_minid_kwarg(store: RedisStreamStore) -> None:
    """append/append_many 接受 minid 參數不拋（minid=0 → 不修剪任何筆）。"""
    eid = await store.append("s_mid", {"k": "v"}, minid=0)
    assert isinstance(eid, str)


async def test_append_many_accepts_minid_kwarg(store: RedisStreamStore) -> None:
    """append_many 接受 minid 關鍵字參數不拋。"""
    ids = await store.append_many("s_mid2", [{"k": "v"}, {"k": "v2"}], minid=0)
    assert len(ids) == 2


async def test_append_many_minid_trims_old_entries() -> None:
    """append_many 帶 minid → Stream ID 小於 minid 的舊筆被修剪，新筆保留。

    透過 raw FakeRedis 注入「遠古」ID（1000-0、2000-0），再呼叫 append_many(minid=5000)。
    minid=5000 → 修剪 ID < 5000 的筆；新筆 auto-ID ≈ 現在毫秒（>> 5000），應保留。
    """
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis()
    s = RedisStreamStore(redis)

    # 注入舊筆（顯式 ID，遠小於 minid=5000）
    await redis.xadd("s_minid_trim", {"n": "old1"}, id="1000-0")
    await redis.xadd("s_minid_trim", {"n": "old2"}, id="2000-0")

    page_before = await s.query("s_minid_trim", limit=100)
    assert len(page_before.items) == 2

    # 寫入新筆，同時帶 minid=5000 修剪 ID < 5000 的舊筆
    await s.append_many("s_minid_trim", [{"n": "new"}], minid=5000)

    page_after = await s.query("s_minid_trim", limit=100)
    # old1(1000-0)、old2(2000-0) 均 < 5000，應被修剪；新筆（now ms >> 5000）保留
    assert len(page_after.items) == 1
    assert page_after.items[0]["n"] == "new"


async def test_query_cursor_no_repeat_no_miss(store: RedisStreamStore) -> None:
    for i in range(6):
        await store.append("s5", {"n": str(i)})

    all_items: list[dict] = []
    cursor = None
    while True:
        page: Page[dict] = await store.query("s5", cursor=cursor, limit=3)
        all_items.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(all_items) == 6
    ids = [item["_id"] for item in all_items]
    assert ids == sorted(ids)
