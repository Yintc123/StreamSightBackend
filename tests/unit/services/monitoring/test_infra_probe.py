"""InfraProbe + 計算純函式單元測試（infra-monitoring.md §6.1/§6.2）。"""

import httpx
import pytest

from app.services.monitoring.infra_probe import (
    InfraProbe,
    InfraProbeError,
    compute_buffer_pool_hit_rate,
    compute_cpu_percent,
    compute_disk_percent,
    compute_iops,
    compute_memory_percent,
)

# ── Prometheus text fixtures ──────────────────────────────────────────────────

NODE_METRICS = """\
# HELP node_cpu_seconds_total Seconds the CPUs spent in each mode.
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 100.0
node_cpu_seconds_total{cpu="0",mode="user"} 20.0
node_cpu_seconds_total{cpu="0",mode="system"} 10.0
node_cpu_seconds_total{cpu="1",mode="idle"} 200.0
node_cpu_seconds_total{cpu="1",mode="user"} 30.0
node_cpu_seconds_total{cpu="1",mode="system"} 15.0
# HELP node_memory_MemAvailable_bytes Memory information field MemAvailable_bytes.
# TYPE node_memory_MemAvailable_bytes gauge
node_memory_MemAvailable_bytes 3221225472.0
# HELP node_memory_MemTotal_bytes Memory information field MemTotal_bytes.
# TYPE node_memory_MemTotal_bytes gauge
node_memory_MemTotal_bytes 8589934592.0
# HELP node_filesystem_avail_bytes Filesystem space available to non-root users in bytes.
# TYPE node_filesystem_avail_bytes gauge
node_filesystem_avail_bytes{device="/dev/sda1",fstype="ext4",mountpoint="/"} 53687091200.0
node_filesystem_avail_bytes{device="tmpfs",fstype="tmpfs",mountpoint="/"} 1073741824.0
# HELP node_filesystem_size_bytes Filesystem size in bytes.
# TYPE node_filesystem_size_bytes gauge
node_filesystem_size_bytes{device="/dev/sda1",fstype="ext4",mountpoint="/"} 107374182400.0
node_filesystem_size_bytes{device="tmpfs",fstype="tmpfs",mountpoint="/"} 2147483648.0
# HELP node_disk_reads_completed_total The total number of reads completed successfully.
# TYPE node_disk_reads_completed_total counter
node_disk_reads_completed_total{device="sda"} 5000.0
node_disk_reads_completed_total{device="sdb"} 3000.0
# HELP node_disk_writes_completed_total The total number of writes completed successfully.
# TYPE node_disk_writes_completed_total counter
node_disk_writes_completed_total{device="sda"} 2000.0
node_disk_writes_completed_total{device="sdb"} 1000.0
"""

NODE_METRICS_ALL_VIRTUAL_FS = """\
node_cpu_seconds_total{cpu="0",mode="idle"} 100.0
node_cpu_seconds_total{cpu="0",mode="user"} 20.0
node_memory_MemAvailable_bytes 3221225472.0
node_memory_MemTotal_bytes 8589934592.0
node_filesystem_avail_bytes{device="tmpfs",fstype="tmpfs",mountpoint="/"} 1073741824.0
node_filesystem_size_bytes{device="tmpfs",fstype="tmpfs",mountpoint="/"} 2147483648.0
node_disk_reads_completed_total{device="sda"} 1000.0
node_disk_writes_completed_total{device="sda"} 500.0
"""

MYSQL_METRICS = """\
# HELP mysql_global_status_threads_connected Generic metric from SHOW GLOBAL STATUS.
# TYPE mysql_global_status_threads_connected untyped
mysql_global_status_threads_connected 5.0
# HELP mysql_global_status_innodb_buffer_pool_reads Generic metric from SHOW GLOBAL STATUS.
# TYPE mysql_global_status_innodb_buffer_pool_reads untyped
mysql_global_status_innodb_buffer_pool_reads 100.0
# HELP mysql_global_status_innodb_buffer_pool_read_requests Generic metric from SHOW GLOBAL STATUS.
# TYPE mysql_global_status_innodb_buffer_pool_read_requests untyped
mysql_global_status_innodb_buffer_pool_read_requests 10000.0
"""


def _make_probe(node_body: str = NODE_METRICS, mysql_body: str = MYSQL_METRICS) -> InfraProbe:
    def handler(request: httpx.Request) -> httpx.Response:
        if "9100" in str(request.url):
            return httpx.Response(200, text=node_body, headers={"content-type": "text/plain"})
        return httpx.Response(200, text=mysql_body, headers={"content-type": "text/plain"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return InfraProbe("http://node-exporter:9100", "http://mysqld-exporter:9104", client)


# ── InfraProbe.fetch_node_metrics ────────────────────────────────────────────


async def test_fetch_node_metrics_returns_all_keys() -> None:
    """§6.1：fetch_node_metrics 回傳 §3.2 所定義的所有 key。"""
    probe = _make_probe()
    result = await probe.fetch_node_metrics()

    assert "cpu_idle_total" in result
    assert "cpu_all_total" in result
    assert "mem_available" in result
    assert "mem_total" in result
    assert "disk_avail" in result
    assert "disk_size" in result
    assert "disk_reads_total" in result
    assert "disk_writes_total" in result


async def test_fetch_node_metrics_multi_core_aggregation() -> None:
    """§6.1：多 core — cpu_idle_total = 所有 core idle 加總，cpu_all_total = 所有 core 所有 mode 加總。"""
    probe = _make_probe()
    result = await probe.fetch_node_metrics()

    assert result["cpu_idle_total"] == pytest.approx(300.0)  # 100 + 200
    assert result["cpu_all_total"] == pytest.approx(375.0)  # 130 + 245


async def test_fetch_node_metrics_fstype_filter_excludes_virtual() -> None:
    """§6.1：同一 mountpoint="/" 有 ext4 + tmpfs → 取 ext4（排除虛擬 fs）。"""
    probe = _make_probe()
    result = await probe.fetch_node_metrics()

    assert result["disk_avail"] == pytest.approx(53687091200.0)
    assert result["disk_size"] == pytest.approx(107374182400.0)


async def test_fetch_node_metrics_all_virtual_fs_returns_zeros() -> None:
    """§6.1：mountpoint="/" 全是虛擬 fs → disk_avail = 0, disk_size = 0。"""
    probe = _make_probe(node_body=NODE_METRICS_ALL_VIRTUAL_FS)
    result = await probe.fetch_node_metrics()

    assert result["disk_avail"] == 0
    assert result["disk_size"] == 0


async def test_fetch_node_metrics_disk_reads_aggregated() -> None:
    """§6.1：disk_reads_total / disk_writes_total 為所有 device 加總。"""
    probe = _make_probe()
    result = await probe.fetch_node_metrics()

    assert result["disk_reads_total"] == pytest.approx(8000.0)  # 5000 + 3000
    assert result["disk_writes_total"] == pytest.approx(3000.0)  # 2000 + 1000


# ── InfraProbe.fetch_mysql_metrics ───────────────────────────────────────────


async def test_fetch_mysql_metrics_returns_all_keys() -> None:
    """§6.1：fetch_mysql_metrics 回傳 §3.3 所定義的所有 key。"""
    probe = _make_probe()
    result = await probe.fetch_mysql_metrics()

    assert result["db_connections"] == pytest.approx(5.0)
    assert result["innodb_reads"] == pytest.approx(100.0)
    assert result["innodb_read_requests"] == pytest.approx(10000.0)


async def test_fetch_node_metrics_5xx_raises_probe_error() -> None:
    """§6.1：exporter 回 5xx → 拋 InfraProbeError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    probe = InfraProbe("http://node-exporter:9100", "http://mysqld-exporter:9104", client)

    with pytest.raises(InfraProbeError):
        await probe.fetch_node_metrics()


async def test_fetch_mysql_metrics_5xx_raises_probe_error() -> None:
    """§6.1：mysqld-exporter 回 5xx → 拋 InfraProbeError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "9100" in str(request.url):
            return httpx.Response(200, text=NODE_METRICS, headers={"content-type": "text/plain"})
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    probe = InfraProbe("http://node-exporter:9100", "http://mysqld-exporter:9104", client)

    with pytest.raises(InfraProbeError):
        await probe.fetch_mysql_metrics()


async def test_fetch_node_metrics_transport_error_raises_probe_error() -> None:
    """§6.1：連線失敗（TransportError）→ 拋 InfraProbeError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    probe = InfraProbe("http://node-exporter:9100", "http://mysqld-exporter:9104", client)

    with pytest.raises(InfraProbeError):
        await probe.fetch_node_metrics()


async def test_aclose_closes_client() -> None:
    """§6.1：aclose() 後 underlying AsyncClient 已關閉。"""
    probe = _make_probe()
    assert not probe._client.is_closed
    await probe.aclose()
    assert probe._client.is_closed


# ── 計算純函式（§6.2）────────────────────────────────────────────────────────


def test_compute_cpu_percent_normal() -> None:
    prev = {"cpu_idle_total": 0.0, "cpu_all_total": 0.0}
    curr = {"cpu_idle_total": 100.0, "cpu_all_total": 200.0}
    assert compute_cpu_percent(prev, curr) == pytest.approx(50.0)


def test_compute_cpu_percent_prev_none_returns_none() -> None:
    curr = {"cpu_idle_total": 100.0, "cpu_all_total": 200.0}
    assert compute_cpu_percent(None, curr) is None


def test_compute_cpu_percent_delta_total_zero_returns_none() -> None:
    prev = {"cpu_idle_total": 100.0, "cpu_all_total": 200.0}
    curr = {"cpu_idle_total": 100.0, "cpu_all_total": 200.0}
    assert compute_cpu_percent(prev, curr) is None


def test_compute_cpu_percent_all_idle_returns_zero() -> None:
    prev = {"cpu_idle_total": 0.0, "cpu_all_total": 0.0}
    curr = {"cpu_idle_total": 100.0, "cpu_all_total": 100.0}
    assert compute_cpu_percent(prev, curr) == pytest.approx(0.0)


def test_compute_iops_normal() -> None:
    assert compute_iops(1000.0, 1050.0, 5.0) == pytest.approx(10.0)


def test_compute_iops_prev_none_returns_none() -> None:
    assert compute_iops(None, 1050.0, 5.0) is None


def test_compute_iops_interval_zero_returns_none() -> None:
    assert compute_iops(1000.0, 1050.0, 0.0) is None


def test_compute_memory_percent_normal() -> None:
    assert compute_memory_percent(3221225472.0, 8589934592.0) == pytest.approx(62.5)


def test_compute_memory_percent_total_zero_returns_zero() -> None:
    assert compute_memory_percent(0.0, 0.0) == 0.0


def test_compute_disk_percent_normal() -> None:
    assert compute_disk_percent(53687091200.0, 107374182400.0) == pytest.approx(50.0)


def test_compute_disk_percent_size_zero_returns_zero() -> None:
    assert compute_disk_percent(0.0, 0.0) == 0.0


def test_compute_buffer_pool_hit_rate_normal() -> None:
    assert compute_buffer_pool_hit_rate(100.0, 10000.0) == pytest.approx(99.0)


def test_compute_buffer_pool_hit_rate_read_requests_zero_returns_none() -> None:
    assert compute_buffer_pool_hit_rate(0.0, 0.0) is None
