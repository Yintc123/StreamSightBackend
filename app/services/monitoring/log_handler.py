"""RedisStreamLogHandler：非阻塞 emit + 背景 flush task（monitoring.md §2.3）。

emit() 只 put_nowait 進有界佇列（fire-and-forget）。
背景 flush task 批次 XADD 進 monitor:stream:logs。
best-effort：佇列滿丟棄、flush 失敗只 log warning、不影響業務。
"""

from __future__ import annotations

import asyncio
import logging

from app.services.monitoring.store import TimeSeriesStore

_logger = logging.getLogger(__name__)


class RedisStreamLogHandler(logging.Handler):
    """emit() 只 put_nowait 進有界佇列（不 await、不阻塞、不拋）。"""

    def __init__(self, queue: asyncio.Queue[dict]) -> None:
        super().__init__()
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            item: dict = {
                "ts": int(record.created * 1000),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
                "request_id": getattr(record, "request_id", None),
                "module": record.module,
                "func": record.funcName,
                "line": record.lineno,
            }
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            pass  # 佇列滿 → 靜默 drop（best-effort）
        except Exception:
            self.handleError(record)  # handleError 內部 print，不拋


async def run_log_flusher(
    queue: asyncio.Queue[dict],
    store: TimeSeriesStore,
    *,
    stream: str,
    maxlen: int,
    batch_size: int,
    interval: float = 1.0,
    flush_once: bool = False,
) -> None:
    """背景 flush task：每 interval 秒或滿 batch_size 則批次 XADD。

    flush_once=True 用於測試（只跑一輪後返回）。
    失敗只 log warning，不中斷（best-effort）。
    """
    while True:
        await asyncio.sleep(0 if flush_once else interval)

        batch: list[dict] = []
        while batch_size and not queue.empty() and len(batch) < batch_size:
            try:
                batch.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        for item in batch:
            try:
                entry = {k: v for k, v in item.items() if v is not None}
                await store.append(stream, entry, maxlen=maxlen)
            except Exception:
                _logger.warning("monitoring log flush failed", exc_info=True)

        if flush_once:
            break
