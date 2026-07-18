"""InfraSampler 單元測試（infra-monitoring.md §6.3）。

fakeredis + FakeProbe（stub）驗 ZADD / ZREMRANGEBYSCORE 語意。
"""

import json

import fakeredis.aioredis
import pytest

from app.services.monitoring.infra_probe import InfraProbeError
from app.services.monitoring.infra_sampler import InfraSampler

REDIS_KEY = "monitoring:infra:history"


class FakeProbe:
    """probe stub：回傳固定 raw dict，模擬 node + mysql metrics。"""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail
        self.closed = False

    async def fetch_node_metrics(self) -> dict:
        if self._fail:
            raise InfraProbeError("probe down")
        return {
            "cpu_idle_total": 1000.0,
            "cpu_all_total": 1200.0,
            "mem_available": 4294967296.0,
            "mem_total": 8589934592.0,
            "disk_avail": 53687091200.0,
            "disk_size": 107374182400.0,
            "disk_reads_total": 5000.0,
            "disk_writes_total": 2000.0,
        }

    async def fetch_mysql_metrics(self) -> dict:
        if self._fail:
            raise InfraProbeError("probe down")
        return {
            "db_connections": 5.0,
            "innodb_reads": 100.0,
            "innodb_read_requests": 10000.0,
        }

    async def aclose(self) -> None:
        self.closed = True


class FakeProbeStep:
    """兩次呼叫回傳不同 cpu 值，用來驗「第二次有 cpu_percent」。"""

    def __init__(self) -> None:
        self._call = 0

    async def fetch_node_metrics(self) -> dict:
        self._call += 1
        base = 1000.0 * self._call
        return {
            "cpu_idle_total": base,
            "cpu_all_total": base + 200.0,
            "mem_available": 4294967296.0,
            "mem_total": 8589934592.0,
            "disk_avail": 53687091200.0,
            "disk_size": 107374182400.0,
            "disk_reads_total": 5000.0 * self._call,
            "disk_writes_total": 2000.0 * self._call,
        }

    async def fetch_mysql_metrics(self) -> dict:
        return {"db_connections": 5.0, "innodb_reads": 100.0, "innodb_read_requests": 10000.0}

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _make_sampler(
    probe: FakeProbe | FakeProbeStep,
    fake_redis: fakeredis.aioredis.FakeRedis,
    *,
    retention_hours: int = 24,
) -> InfraSampler:
    return InfraSampler(
        probe=probe,
        redis=fake_redis,
        redis_key=REDIS_KEY,
        interval_seconds=5,
        retention_hours=retention_hours,
    )


async def test_tick_writes_one_entry(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """§6.3：_tick() 一次 → Redis Sorted Set 有 1 筆，反序列化後符合 InfraSnapshot 結構。"""
    sampler = _make_sampler(FakeProbe(), fake_redis)
    await sampler._tick()

    assert await fake_redis.zcard(REDIS_KEY) == 1
    members = await fake_redis.zrange(REDIS_KEY, 0, -1, withscores=True)
    data = json.loads(str(members[0][0]))
    assert "ts" in data
    assert "memory_percent" in data
    assert "disk_percent" in data


async def test_tick_score_equals_ts(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """§6.3：寫入的 score 等於快照的 ts 欄位。"""
    sampler = _make_sampler(FakeProbe(), fake_redis)
    await sampler._tick()

    members = await fake_redis.zrange(REDIS_KEY, 0, -1, withscores=True)
    json_str, score = members[0]
    data = json.loads(str(json_str))
    assert int(score) == data["ts"]


async def test_tick_zremrangebyscore_clears_old_entries(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """§6.3：ZREMRANGEBYSCORE 生效：超出 retention 的舊資料被清除。"""
    import time

    now_ms = int(time.time() * 1000)
    old_ts = now_ms - (25 * 3600 * 1000)  # 25 小時前（超出 24h 保留）
    old_json = json.dumps({"ts": old_ts, "memory_percent": 50.0, "disk_percent": 30.0})
    await fake_redis.zadd(REDIS_KEY, {old_json: old_ts})
    assert await fake_redis.zcard(REDIS_KEY) == 1

    sampler = _make_sampler(FakeProbe(), fake_redis, retention_hours=24)
    await sampler._tick()

    # 舊資料應被清除，只剩本次寫入的 1 筆
    assert await fake_redis.zcard(REDIS_KEY) == 1
    members = await fake_redis.zrange(REDIS_KEY, 0, -1, withscores=True)
    data = json.loads(str(members[0][0]))
    assert data["ts"] != old_ts


async def test_tick_probe_error_skips_write(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """§6.3：probe 拋 InfraProbeError → 只 log warning，Redis 不新增，循環不中斷。"""
    sampler = _make_sampler(FakeProbe(fail=True), fake_redis)
    await sampler._tick()

    assert await fake_redis.zcard(REDIS_KEY) == 0


async def test_tick_probe_error_then_success(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """§6.3：probe 失敗後，下次成功時循環可繼續正常寫入。"""
    fail_probe = FakeProbe(fail=True)
    sampler = _make_sampler(fail_probe, fake_redis)
    await sampler._tick()
    assert await fake_redis.zcard(REDIS_KEY) == 0

    sampler._probe = FakeProbe(fail=False)
    await sampler._tick()
    assert await fake_redis.zcard(REDIS_KEY) == 1


async def test_first_tick_cpu_is_null(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """§6.3：第一次 _tick() → cpu_percent 為 null，disk_read_iops 為 null。"""
    sampler = _make_sampler(FakeProbeStep(), fake_redis)
    await sampler._tick()

    members = await fake_redis.zrange(REDIS_KEY, 0, -1)
    data = json.loads(str(members[0]))
    assert data["cpu_percent"] is None
    assert data["disk_read_iops"] is None


async def test_second_tick_cpu_not_null(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """§6.3：連續兩次 _tick() → 至少一筆 cpu_percent 不為 null（前次快照存在）。

    注意：兩次 tick 在同毫秒內執行時 score 相同，Sorted Set 依 member 字典序排列，
    不保證「第二筆」的 index。改驗「任一筆有值」，語意等價。
    """
    sampler = _make_sampler(FakeProbeStep(), fake_redis)
    await sampler._tick()
    await sampler._tick()

    members = await fake_redis.zrange(REDIS_KEY, 0, -1)
    snapshots = [json.loads(str(m)) for m in members]
    assert any(s["cpu_percent"] is not None for s in snapshots)
    assert any(s["disk_read_iops"] is not None for s in snapshots)
