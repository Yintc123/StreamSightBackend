"""InfraSampler：background task，每 interval_seconds 採集一次並寫入 Redis Sorted Set。

（infra-monitoring.md §4.2/§2.2/§2.3）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import redis.asyncio as redis

from app.dtos.monitoring import InfraSnapshot
from app.services.monitoring.infra_probe import (
    InfraProbeError,
    compute_buffer_pool_hit_rate,
    compute_cpu_percent,
    compute_disk_percent,
    compute_iops,
    compute_memory_percent,
)


class _ProbeProtocol(Protocol):
    async def fetch_node_metrics(self) -> dict: ...
    async def fetch_mysql_metrics(self) -> dict: ...
    async def aclose(self) -> None: ...


logger = logging.getLogger(__name__)


class InfraSampler:
    """每 interval_seconds 秒採集 OS / DB 指標，寫入 Redis Sorted Set。

    Args:
        probe: InfraProbe 實例（或相容 stub）
        redis: redis.asyncio.Redis 實例（直接注入，§2.10）
        redis_key: Sorted Set key
        interval_seconds: 採集週期
        retention_hours: 保留時長（小時）；超出的舊資料由 ZREMRANGEBYSCORE 清除
    """

    def __init__(
        self,
        probe: _ProbeProtocol,
        redis: redis.Redis,
        redis_key: str,
        interval_seconds: int,
        retention_hours: int,
    ) -> None:
        self._probe = probe
        self._redis = redis
        self._redis_key = redis_key
        self._interval = interval_seconds
        self._retention_ms = retention_hours * 3600 * 1000
        self._prev_node: dict | None = None
        self._prev_ts: float | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="infra-sampler")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        await self._probe.aclose()

    async def _loop(self) -> None:
        while True:
            await self._tick()
            await asyncio.sleep(self._interval)

    async def _tick(self) -> None:
        now_ts = time.time()
        now_ms = int(now_ts * 1000)
        interval = (now_ts - self._prev_ts) if self._prev_ts is not None else float(self._interval)

        try:
            node = await self._probe.fetch_node_metrics()
            mysql = await self._probe.fetch_mysql_metrics()
        except InfraProbeError as exc:
            logger.warning("InfraSampler: probe error, skipping tick: %s", exc)
            return

        cpu_percent = compute_cpu_percent(self._prev_node, node)
        memory_percent = compute_memory_percent(node["mem_available"], node["mem_total"])
        disk_percent = compute_disk_percent(node["disk_avail"], node["disk_size"])

        prev_reads = self._prev_node["disk_reads_total"] if self._prev_node else None
        prev_writes = self._prev_node["disk_writes_total"] if self._prev_node else None
        disk_read_iops = compute_iops(prev_reads, node["disk_reads_total"], interval)
        disk_write_iops = compute_iops(prev_writes, node["disk_writes_total"], interval)

        db_connections = int(mysql["db_connections"]) if mysql["db_connections"] else None
        db_buffer_pool_hit_rate = compute_buffer_pool_hit_rate(
            mysql["innodb_reads"], mysql["innodb_read_requests"]
        )

        snapshot = InfraSnapshot(
            ts=now_ms,
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            disk_read_iops=disk_read_iops,
            disk_write_iops=disk_write_iops,
            db_connections=db_connections,
            db_buffer_pool_hit_rate=db_buffer_pool_hit_rate,
        )

        json_str = json.dumps(snapshot.model_dump())
        await self._redis.zadd(self._redis_key, {json_str: now_ms})
        cutoff_ms = now_ms - self._retention_ms
        await self._redis.zremrangebyscore(self._redis_key, 0, cutoff_ms)

        self._prev_node = node
        self._prev_ts = now_ts
