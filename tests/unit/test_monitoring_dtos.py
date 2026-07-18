"""monitoring DTO 驗證（monitoring.md §3）。"""

from app.dtos.monitoring import DbSample, LogEntry, Page


def test_log_entry_required_fields() -> None:
    entry = LogEntry(ts=1730000000123, level="INFO", logger="app.auth", message="ok")
    assert entry.ts == 1730000000123
    assert entry.level == "INFO"
    assert entry.request_id is None


def test_log_entry_optional_fields() -> None:
    entry = LogEntry(
        ts=1730000000123,
        level="ERROR",
        logger="app.auth",
        message="fail",
        request_id="req-abc",
        module="auth",
        func="login",
        line=42,
    )
    assert entry.request_id == "req-abc"
    assert entry.line == 42


def test_db_sample_required_fields() -> None:
    sample = DbSample(
        ts=1730000000000,
        pool={"size": 10, "checked_out": 2, "overflow": 0, "checked_in": 8},
        connections={"connected": 5, "running": 2, "idle": 3},
        backend="mariadb",
    )
    assert sample.ts == 1730000000000
    assert sample.db_size_bytes is None
    assert sample.longest_query_seconds is None
    assert sample.backend == "mariadb"


def test_db_sample_sqlite_backend() -> None:
    sample = DbSample(
        ts=1000,
        pool={"size": 1, "checked_out": 0, "overflow": 0, "checked_in": 1},
        connections={"connected": 0, "running": 0, "idle": 0},
        backend="sqlite",
    )
    assert sample.backend == "sqlite"


def test_page_empty() -> None:
    page: Page[LogEntry] = Page(items=[], next_cursor=None)
    assert page.items == []
    assert page.next_cursor is None


def test_page_with_items() -> None:
    entry = LogEntry(ts=1, level="INFO", logger="x", message="m")
    page: Page[LogEntry] = Page(items=[entry], next_cursor="1730000000123-0")
    assert len(page.items) == 1
    assert page.next_cursor == "1730000000123-0"
