"""MonitoringSampler：leader lease + 採樣 → 落地 + 推播（monitoring.md §2.5/§7.5）。"""

import fakeredis.aioredis
import pytest

from app.services.monitoring.sampler import MonitoringSampler
from app.services.monitoring.store import RedisStreamStore
from app.services.ws.publisher import Publisher

DB_STREAM = "monitor:stream:db"


class FakeProbe:
    def __init__(self) -> None:
        self.call_count = 0

    async def sample(self) -> dict:
        self.call_count += 1
        return {
            "ts": 1000,
            "pool": {"size": 5, "checked_out": 1, "overflow": 0, "checked_in": 4},
            "connections": {"connected": 2, "running": 1, "idle": 1},
            "backend": "pool_only",
        }


class ErrorProbe:
    async def sample(self) -> dict:
        raise RuntimeError("probe down")


@pytest.fixture
def fake_redis_client() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis()


async def test_one_tick_appends_to_store(fake_redis_client: fakeredis.aioredis.FakeRedis) -> None:
    """一輪採樣 → Store.append 有一筆（monitoring.md §7.5）。"""
    store = RedisStreamStore(fake_redis_client)
    publisher = Publisher(fake_redis_client)
    probe = FakeProbe()

    sampler = MonitoringSampler(
        client=fake_redis_client,
        probe=probe,
        store=store,
        publisher=publisher,
        stream=DB_STREAM,
        maxlen=1000,
        sample_interval=0.01,
        lease_seconds=1,
    )
    await sampler._tick()  # 手動觸發一輪（不 sleep）

    page = await store.query(DB_STREAM, limit=10)
    assert len(page.items) == 1
    assert probe.call_count == 1


async def test_leader_lease_only_one_samples(
    fake_redis_client: fakeredis.aioredis.FakeRedis,
) -> None:
    """兩個 sampler 共用同一 fakeredis → 同一輪只有一個採樣（另一個未搶到 lease）。"""
    store = RedisStreamStore(fake_redis_client)
    publisher = Publisher(fake_redis_client)
    probe_a = FakeProbe()
    probe_b = FakeProbe()

    sampler_a = MonitoringSampler(
        client=fake_redis_client,
        probe=probe_a,
        store=store,
        publisher=publisher,
        stream=DB_STREAM,
        maxlen=1000,
        sample_interval=0.01,
        lease_seconds=60,
        instance_id="instance-A",
    )
    sampler_b = MonitoringSampler(
        client=fake_redis_client,
        probe=probe_b,
        store=store,
        publisher=publisher,
        stream=DB_STREAM,
        maxlen=1000,
        sample_interval=0.01,
        lease_seconds=60,
        instance_id="instance-B",
    )

    await sampler_a._tick()
    await sampler_b._tick()

    assert probe_a.call_count + probe_b.call_count == 1


DB_SORTED_SET = "monitoring:db:history"


async def test_tick_writes_to_sorted_set(fake_redis_client: fakeredis.aioredis.FakeRedis) -> None:
    """_tick() 同時寫入 Sorted Set（ZADD），score 等於 ts。"""
    store = RedisStreamStore(fake_redis_client)
    publisher = Publisher(fake_redis_client)

    sampler = MonitoringSampler(
        client=fake_redis_client,
        probe=FakeProbe(),
        store=store,
        publisher=publisher,
        stream=DB_STREAM,
        maxlen=1000,
        sample_interval=0.01,
        lease_seconds=1,
        sorted_set_key=DB_SORTED_SET,
        retention_hours=24,
    )
    await sampler._tick()

    count = await fake_redis_client.zcard(DB_SORTED_SET)
    assert count == 1
    members = await fake_redis_client.zrange(DB_SORTED_SET, 0, -1, withscores=True)
    _, score = members[0]
    assert int(score) == 1000  # FakeProbe returns ts=1000


async def test_tick_sorted_set_clears_old_entries(
    fake_redis_client: fakeredis.aioredis.FakeRedis,
) -> None:
    """_tick() 同時執行 ZREMRANGEBYSCORE，清除超出 retention 的舊資料。"""
    import json
    import time

    now_ms = int(time.time() * 1000)
    old_ts = now_ms - (25 * 3600 * 1000)  # 25 小時前
    old_json = json.dumps({"ts": old_ts, "pool": {}, "connections": {}, "backend": "x"})
    await fake_redis_client.zadd(DB_SORTED_SET, {old_json: old_ts})
    assert await fake_redis_client.zcard(DB_SORTED_SET) == 1

    class NowProbe:
        async def sample(self) -> dict:
            return {
                "ts": now_ms,
                "pool": {"size": 5, "checked_out": 1, "overflow": 0, "checked_in": 4},
                "connections": {"connected": 2, "running": 1, "idle": 1},
                "backend": "pool_only",
            }

    store = RedisStreamStore(fake_redis_client)
    publisher = Publisher(fake_redis_client)
    sampler = MonitoringSampler(
        client=fake_redis_client,
        probe=NowProbe(),
        store=store,
        publisher=publisher,
        stream=DB_STREAM,
        maxlen=1000,
        sample_interval=0.01,
        lease_seconds=1,
        sorted_set_key=DB_SORTED_SET,
        retention_hours=24,
    )
    await sampler._tick()

    # 舊資料被清除，只剩本次寫入
    assert await fake_redis_client.zcard(DB_SORTED_SET) == 1
    members = await fake_redis_client.zrange(DB_SORTED_SET, 0, -1, withscores=True)
    _, score = members[0]
    assert int(score) == now_ms


async def test_tick_no_sorted_set_when_key_not_set(
    fake_redis_client: fakeredis.aioredis.FakeRedis,
) -> None:
    """sorted_set_key 未設定時，不寫 Sorted Set（向後相容）。"""
    store = RedisStreamStore(fake_redis_client)
    publisher = Publisher(fake_redis_client)
    sampler = MonitoringSampler(
        client=fake_redis_client,
        probe=FakeProbe(),
        store=store,
        publisher=publisher,
        stream=DB_STREAM,
        maxlen=1000,
        sample_interval=0.01,
        lease_seconds=1,
        # sorted_set_key 不傳
    )
    await sampler._tick()

    assert await fake_redis_client.zcard(DB_SORTED_SET) == 0


async def test_probe_error_does_not_interrupt_loop(
    fake_redis_client: fakeredis.aioredis.FakeRedis,
) -> None:
    """採樣中 probe 拋例外 → 只 log、循環不中斷（best-effort）。"""
    store = RedisStreamStore(fake_redis_client)
    publisher = Publisher(fake_redis_client)

    sampler = MonitoringSampler(
        client=fake_redis_client,
        probe=ErrorProbe(),
        store=store,
        publisher=publisher,
        stream=DB_STREAM,
        maxlen=1000,
        sample_interval=0.01,
        lease_seconds=1,
    )
    # 不拋
    await sampler._tick()
