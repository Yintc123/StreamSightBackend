"""即時串流資料生成器 — 每秒採樣並發佈至 realtime.stream topic。

（realtime-stream.md §5.2 / realtime-history.md §5.2）

Session 策略：
  - 測試：直接注入 mock repo（repo= 參數），不建立真實 session。
  - Production：注入 session_factory（AsyncSessionLocal），每次 _flush 開一個
    短命 session → bulk_insert → commit → 關閉，避免長存 session stale。
兩個參數互斥；repo 優先（給測試用，不走 factory）。
"""

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.ws.publisher import Publisher

if TYPE_CHECKING:
    from app.repositories.repo_realtime_reading import RealtimeReadingRepository

logger = logging.getLogger(__name__)

STREAM_TOPIC = "realtime.stream"
_TICK_KEY = "realtime:tick"
BATCH_SIZE = 60


def sample_value(tick: int, seed: int = 0) -> float:
    """決定性取值（同前端 lib/realtime.py::sample_value，realtime-stream.md §2.2）。

    digest = SHA-256(f"{seed}:{tick}")
    fraction = int(digest[:8], 16) / 0xFFFFFFFF
    value = round(fraction * 100, 1)  # [0.0, 100.0]，一位小數
    """
    digest = hashlib.sha256(f"{seed}:{tick}".encode()).hexdigest()
    return round(int(digest[:8], 16) / 0xFFFFFFFF * 100, 1)


class RealtimeStreamer:
    """每秒生成模擬值並發佈到 realtime.stream topic，同時批次落地 DB。

    - WS 推送：每秒一次（realtime-stream.md §5.2）
    - DB 落地：滿 BATCH_SIZE(60) 筆時批次 INSERT（realtime-history.md §5.2）
    - start()/stop() 模式對齊 MonitoringSampler / InfraSampler。
    """

    def __init__(
        self,
        publisher: Publisher,
        redis_client: redis.Redis,
        repo: "RealtimeReadingRepository | None" = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._publisher = publisher
        self._redis = redis_client
        self._repo = repo  # 測試注入（mock）
        self._session_factory = session_factory  # production：每次 flush 開新 session
        self._batch: list[dict] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="realtime-streamer")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _tick(self) -> None:
        """一輪採樣：INCR tick → 推 WS → 加入 batch → 滿 60 flush。"""
        tick = int(await self._redis.incr(_TICK_KEY))
        ts = datetime.now(UTC).replace(tzinfo=None)  # naive UTC 存 DB
        value = sample_value(tick)

        await self._publisher.to_topic(
            STREAM_TOPIC,
            {
                "type": "data",
                "topic": STREAM_TOPIC,
                "value": value,
                "ts": datetime.now(UTC).isoformat(),  # 帶 +00:00 給 WS client
            },
        )

        if self._repo is not None or self._session_factory is not None:
            self._batch.append({"value": value, "ts": ts})
            if len(self._batch) >= BATCH_SIZE:
                await self._flush()

    async def _flush(self) -> None:
        """批次寫入 DB；失敗時保留 batch（不 clear）讓下次一起補寫。"""
        if not self._batch:
            return
        try:
            if self._repo is not None:
                # 測試路徑：直接用注入的 repo（mock 或 unit test db_session）
                await self._repo.bulk_insert(list(self._batch))
            elif self._session_factory is not None:
                # Production 路徑：每次 flush 開一個短命 session
                from app.repositories.repo_realtime_reading import RealtimeReadingRepository

                async with self._session_factory() as session:
                    repo = RealtimeReadingRepository(session)
                    await repo.bulk_insert(list(self._batch))
                    await session.commit()
            self._batch.clear()
        except Exception:
            logger.exception("realtime_streamer: flush failed, batch retained for retry")

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.0)
                await self._tick()
            except asyncio.CancelledError:
                await self._flush()  # shutdown 前 flush 剩餘資料
                raise
            except Exception:
                logger.exception("realtime_streamer: error, skip tick")
