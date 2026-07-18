"""MonitoringSampler：lifespan 啟停 + Redis leader lease（monitoring.md §2.5）。

多實例正確性：Redis SET NX EX 搶 leader，只有 leader 採樣，避免 N 倍寫入/重複推播。
best-effort：採樣/落地/推播失敗只 log warning，循環不中斷。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from redis.asyncio import Redis

from app.services.monitoring.db_probe import DbStatsProbe
from app.services.monitoring.store import TimeSeriesStore
from app.services.ws.publisher import Publisher

_logger = logging.getLogger(__name__)

_LEADER_KEY = "monitor:sampler:leader"


class MonitoringSampler:
    def __init__(
        self,
        client: Redis,
        probe: DbStatsProbe,
        store: TimeSeriesStore,
        publisher: Publisher,
        *,
        stream: str,
        maxlen: int,
        sample_interval: float,
        lease_seconds: int,
        instance_id: str | None = None,
    ) -> None:
        self._client = client
        self._probe = probe
        self._store = store
        self._publisher = publisher
        self._stream = stream
        self._maxlen = maxlen
        self._interval = sample_interval
        self._lease = lease_seconds
        self._instance_id = instance_id or str(uuid.uuid4())
        self._task: asyncio.Task | None = None

    async def _acquire_lease(self) -> bool:
        """以 SET NX EX 搶 leader lease；已是 leader 則續租。"""
        acquired = await self._client.set(_LEADER_KEY, self._instance_id, nx=True, ex=self._lease)
        if acquired:
            return True
        current = await self._client.get(_LEADER_KEY)
        current_str = current.decode() if isinstance(current, bytes) else current
        if current_str and current_str == self._instance_id:
            await self._client.expire(_LEADER_KEY, self._lease)
            return True
        return False

    async def _release_lease(self) -> None:
        current = await self._client.get(_LEADER_KEY)
        current_str = current.decode() if isinstance(current, bytes) else current
        if current_str and current_str == self._instance_id:
            await self._client.delete(_LEADER_KEY)

    async def _tick(self) -> None:
        """一輪採樣（可手動呼叫做測試）。"""
        if not await self._acquire_lease():
            return
        try:
            data = await self._probe.sample()
            await self._store.append(self._stream, data, maxlen=self._maxlen)
            await self._publisher.to_topic(
                "monitor.db",
                {"type": "event", "topic": "monitor.db", "ts": data.get("ts", 0), "data": data},
            )
        except Exception:
            _logger.warning("MonitoringSampler tick failed", exc_info=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning("MonitoringSampler loop error", exc_info=True)
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="monitoring-sampler")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        await self._release_lease()
