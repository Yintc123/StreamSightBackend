"""InfraProbe：輪詢 node-exporter / mysqld-exporter 取得 raw 指標（infra-monitoring.md §4.2/§2.6）。

計算純函式與 I/O 分離：InfraProbe 只負責 HTTP + Prometheus text 解析，
compute_* 函式接受純 Python dict/float，易於單元測試。
"""

from __future__ import annotations

import httpx
from prometheus_client.parser import text_string_to_metric_families

_VIRTUAL_FSTYPES = {"tmpfs", "overlay", "squashfs", "devtmpfs", "ramfs"}


class InfraProbeError(Exception):
    """exporter 不可用或回應異常時拋出（infra-monitoring.md §2.6）。"""


class InfraProbe:
    """輪詢 node-exporter / mysqld-exporter，回傳結構化 raw dict。

    Args:
        node_exporter_url: node-exporter base URL（不含 /metrics）
        mysqld_exporter_url: mysqld-exporter base URL（不含 /metrics）
        client: 外部注入的 httpx.AsyncClient（lifespan 管理生命週期）
    """

    def __init__(
        self,
        node_exporter_url: str,
        mysqld_exporter_url: str,
        client: httpx.AsyncClient,
    ) -> None:
        self._node_url = node_exporter_url.rstrip("/") + "/metrics"
        self._mysql_url = mysqld_exporter_url.rstrip("/") + "/metrics"
        self._client = client

    async def _fetch(self, url: str) -> str:
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as exc:
            raise InfraProbeError(f"HTTP {exc.response.status_code} from {url}") from exc
        except httpx.TransportError as exc:
            raise InfraProbeError(f"Transport error fetching {url}: {exc}") from exc

    async def fetch_node_metrics(self) -> dict:
        """回傳 infra-monitoring.md §3.2 所定義的 raw dict。"""
        text = await self._fetch(self._node_url)
        metrics: dict[str, dict] = {}
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                metrics.setdefault(sample.name, []).append(sample)  # type: ignore[attr-defined]

        cpu_idle_total = sum(
            s.value
            for s in metrics.get("node_cpu_seconds_total", [])
            if s.labels.get("mode") == "idle"
        )
        cpu_all_total = sum(s.value for s in metrics.get("node_cpu_seconds_total", []))
        mem_available = next(
            (s.value for s in metrics.get("node_memory_MemAvailable_bytes", [])), 0.0
        )
        mem_total = next((s.value for s in metrics.get("node_memory_MemTotal_bytes", [])), 0.0)

        # 過濾虛擬 fs，取 mountpoint="/" 第一筆真實 rootfs
        fs_avail_candidates = [
            s
            for s in metrics.get("node_filesystem_avail_bytes", [])
            if s.labels.get("mountpoint") == "/" and s.labels.get("fstype") not in _VIRTUAL_FSTYPES
        ]
        fs_size_candidates = [
            s
            for s in metrics.get("node_filesystem_size_bytes", [])
            if s.labels.get("mountpoint") == "/" and s.labels.get("fstype") not in _VIRTUAL_FSTYPES
        ]
        disk_avail = fs_avail_candidates[0].value if fs_avail_candidates else 0.0
        disk_size = fs_size_candidates[0].value if fs_size_candidates else 0.0

        disk_reads_total = sum(s.value for s in metrics.get("node_disk_reads_completed_total", []))
        disk_writes_total = sum(
            s.value for s in metrics.get("node_disk_writes_completed_total", [])
        )

        return {
            "cpu_idle_total": cpu_idle_total,
            "cpu_all_total": cpu_all_total,
            "mem_available": mem_available,
            "mem_total": mem_total,
            "disk_avail": disk_avail,
            "disk_size": disk_size,
            "disk_reads_total": disk_reads_total,
            "disk_writes_total": disk_writes_total,
        }

    async def fetch_mysql_metrics(self) -> dict:
        """回傳 infra-monitoring.md §3.3 所定義的 raw dict。"""
        text = await self._fetch(self._mysql_url)
        metrics: dict[str, list] = {}
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                metrics.setdefault(sample.name, []).append(sample)  # type: ignore[attr-defined]

        db_connections = next(
            (s.value for s in metrics.get("mysql_global_status_threads_connected", [])), 0.0
        )
        innodb_reads = next(
            (s.value for s in metrics.get("mysql_global_status_innodb_buffer_pool_reads", [])), 0.0
        )
        innodb_read_requests = next(
            (
                s.value
                for s in metrics.get("mysql_global_status_innodb_buffer_pool_read_requests", [])
            ),
            0.0,
        )

        return {
            "db_connections": db_connections,
            "innodb_reads": innodb_reads,
            "innodb_read_requests": innodb_read_requests,
        }

    async def aclose(self) -> None:
        """關閉 underlying httpx.AsyncClient（由 InfraSampler.stop() 呼叫）。"""
        await self._client.aclose()


# ── 計算純函式（無 I/O，易測）────────────────────────────────────────────────


def compute_cpu_percent(prev: dict | None, curr: dict) -> float | None:
    """CPU 使用率（%）：兩次快照差值計算（infra-monitoring.md §2.5）。"""
    if prev is None:
        return None
    delta_idle = curr["cpu_idle_total"] - prev["cpu_idle_total"]
    delta_total = curr["cpu_all_total"] - prev["cpu_all_total"]
    if delta_total == 0:
        return None
    return round((1 - delta_idle / delta_total) * 100, 1)


def compute_iops(prev: float | None, curr: float, interval: float) -> float | None:
    """磁碟 IOPS（次/秒）：累計計數器差值除以間隔秒數。"""
    if prev is None or interval == 0:
        return None
    return round((curr - prev) / interval, 2)


def compute_memory_percent(mem_available: float, mem_total: float) -> float:
    """記憶體使用率（%）：單次快照計算。"""
    if mem_total == 0:
        return 0.0
    return round((1 - mem_available / mem_total) * 100, 1)


def compute_disk_percent(disk_avail: float, disk_size: float) -> float:
    """磁碟使用率（%）：單次快照計算。"""
    if disk_size == 0:
        return 0.0
    return round((1 - disk_avail / disk_size) * 100, 1)


def compute_buffer_pool_hit_rate(innodb_reads: float, innodb_read_requests: float) -> float | None:
    """InnoDB Buffer Pool 命中率（%）：1 - miss/total。"""
    if innodb_read_requests == 0:
        return None
    return round((1 - innodb_reads / innodb_read_requests) * 100, 1)
