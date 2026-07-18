"""Monitoring 跨層 DTO（monitoring.md §3）。"""

from __future__ import annotations

from pydantic import BaseModel


class LogEntry(BaseModel):
    """落 monitor:stream:logs 的一筆日誌（已遮罩，monitoring.md §3.1）。"""

    ts: int
    level: str
    logger: str
    message: str
    request_id: str | None = None
    module: str | None = None
    func: str | None = None
    line: int | None = None


class DbSample(BaseModel):
    """落 monitor:stream:db 的一筆 DB 狀態快照（monitoring.md §3.2）。"""

    ts: int
    pool: dict
    connections: dict
    db_size_bytes: int | None = None
    longest_query_seconds: float | None = None
    backend: str = "unknown"


class Page[T](BaseModel):
    """游標分頁封套（monitoring.md §3.3）。"""

    items: list[T]
    next_cursor: str | None = None
