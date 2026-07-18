"""monitoring DTO 驗證（monitoring.md §3）。"""

from app.dtos.monitoring import DbHistoryResponse, DbSample, LogEntry, Page


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


# ── InfraSnapshot + InfraHistoryResponse（infra-monitoring.md §3.1）──

from app.dtos.monitoring import InfraHistoryResponse, InfraSnapshot  # noqa: E402


def test_infra_snapshot_required_fields() -> None:
    snap = InfraSnapshot(ts=1730000000000, memory_percent=61.2, disk_percent=45.8)
    assert snap.ts == 1730000000000
    assert snap.memory_percent == 61.2
    assert snap.disk_percent == 45.8
    assert snap.cpu_percent is None
    assert snap.disk_read_iops is None
    assert snap.disk_write_iops is None
    assert snap.db_connections is None
    assert snap.db_buffer_pool_hit_rate is None


def test_infra_snapshot_all_fields() -> None:
    snap = InfraSnapshot(
        ts=1730000005000,
        cpu_percent=23.4,
        memory_percent=61.5,
        disk_percent=45.8,
        disk_read_iops=12.0,
        disk_write_iops=8.5,
        db_connections=6,
        db_buffer_pool_hit_rate=98.5,
    )
    assert snap.cpu_percent == 23.4
    assert snap.disk_read_iops == 12.0
    assert snap.db_connections == 6


def test_db_history_response_empty() -> None:
    resp = DbHistoryResponse(snapshots=[])
    assert resp.snapshots == []


def test_db_history_response_with_snapshots() -> None:
    snaps = [
        DbSample(
            ts=1000,
            pool={"size": 5, "checked_out": 1},
            connections={"connected": 2, "running": 1, "idle": 1},
            backend="mariadb",
        ),
        DbSample(
            ts=2000,
            pool={"size": 5, "checked_out": 2},
            connections={"connected": 3, "running": 2, "idle": 1},
            backend="mariadb",
        ),
    ]
    resp = DbHistoryResponse(snapshots=snaps)
    assert len(resp.snapshots) == 2
    assert resp.snapshots[0].ts == 1000
    assert resp.snapshots[1].ts == 2000


def test_infra_history_response_empty() -> None:
    resp = InfraHistoryResponse(snapshots=[])
    assert resp.snapshots == []


def test_infra_history_response_with_snapshots() -> None:
    snaps = [
        InfraSnapshot(ts=1000, memory_percent=50.0, disk_percent=30.0),
        InfraSnapshot(ts=2000, memory_percent=55.0, disk_percent=31.0),
    ]
    resp = InfraHistoryResponse(snapshots=snaps)
    assert len(resp.snapshots) == 2
    assert resp.snapshots[0].ts == 1000
    assert resp.snapshots[1].ts == 2000
