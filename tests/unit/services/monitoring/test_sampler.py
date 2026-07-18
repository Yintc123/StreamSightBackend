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
