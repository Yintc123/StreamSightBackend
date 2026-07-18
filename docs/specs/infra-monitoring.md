# 規格書：Infra Monitoring 模組（基礎設施指標採集）

> 狀態：**已實作（✅ 529 tests 全綠，ruff / pyright 通過）** ／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> **語言**：繁體中文。
>
> 🔗 相關規格：既有 App 層監控（[`monitoring.md`](./monitoring.md)：日誌查詢／DB 連線池狀態／Redis Stream 骨架）。
>
> ⚠️ **範圍**：採集 **OS 硬體層**（CPU / 記憶體 / 磁碟 / IOPS）與 **MariaDB 引擎層**（連線數 / Buffer Pool 命中率）的即時指標，每 5 秒採集一次，以 REST API 提供給 Streamlit 顯示折線圖。
>
> 本模組與 `monitoring.md`（App 層監控）**互補不重疊**：
> - `monitoring.md`：app 程序內部可見的指標（SQLAlchemy pool、`SHOW GLOBAL STATUS`、日誌），走 Redis Stream + WebSocket。
> - 本模組：OS 主機與 MariaDB 引擎對外暴露的指標（node-exporter / mysqld-exporter HTTP endpoint），走輪詢 + Redis Sorted Set + REST。

---

## 0. 功能總覽

**一句話**：FastAPI background task 每 5 秒輪詢 `node-exporter` 與 `mysqld-exporter` 的 `/metrics` endpoint，計算各指標後以 JSON string 存入 Redis Sorted Set（保留最近 24 小時），`GET /admin/monitoring/infra` 支援時間範圍查詢，回傳由舊到新排列的歷史陣列供 Streamlit 繪製折線圖。

| 採集來源 | 指標 | 端點 |
|---|---|---|
| `node-exporter:9100` | CPU 使用率、記憶體使用率、磁碟使用率、磁碟 IOPS | Prometheus text format |
| `mysqld-exporter:9104` | DB 連線數、Buffer Pool 命中率 | Prometheus text format |

**資料流**：

```
FastAPI lifespan background task（每 5s）
  ├── GET node-exporter:9100/metrics   → 解析 → 計算 CPU / 記憶體 / 磁碟 / IOPS
  ├── GET mysqld-exporter:9104/metrics → 解析 → 計算連線數 / Buffer Pool 命中率
  └── ZADD monitoring:infra:history <ts_ms> <InfraSnapshot JSON string>
      ZREMRANGEBYSCORE monitoring:infra:history 0 <now_ms - 24h_ms>  ← 刪除 24 小時前資料

GET /admin/monitoring/infra?start_ms=<epoch_ms>&end_ms=<epoch_ms>
  └── ZRANGEBYSCORE monitoring:infra:history <start_ms> <end_ms>
      → JSON parse 每筆 → 天然由舊到新（score 升冪）→ InfraHistoryResponse
```

---

## 1. 背景與目標

Streamlit 即時監控頁（`pages/realtime_monitor.py`）需要顯示 DB 主機的硬體資源折線圖。OS 層指標（CPU / 記憶體 / 磁碟）只能在 DB 主機上採集，無法從 API server 直接讀取；選用 `node-exporter` + `mysqld-exporter` 作為採集 agent（已加入 `infra/docker-compose.yml`），FastAPI 作為資料聚合層，統一對 Streamlit 提供 REST API。

### 目標

- **採集 OS 層指標**：透過 `node-exporter` 取得 CPU / 記憶體 / 磁碟使用率 / IOPS。
- **採集 MariaDB 引擎指標**：透過 `mysqld-exporter` 取得連線數、Buffer Pool 命中率。
- **所有指標正確計算**：累計計數器（CPU / IOPS）需兩次取樣差值計算，由 background task 維護前次快照。
- **24 小時滾動歷史**：Redis Sorted Set 依時間戳為 score，保留最近 24 小時，供前端自由選取時間段。
- **時間範圍查詢**：`GET /admin/monitoring/infra?start_ms=&end_ms=`，未帶參數時預設回傳最近 1 小時，避免一次傳回全部 24 小時資料（約 17,280 筆）。
- **不依賴 Prometheus**：FastAPI 直接輪詢 exporter endpoint，不需要 Prometheus server。

### 非目標

- **歷史歸檔**：Redis Sorted Set 為有界 hot buffer（24 小時），不做長期儲存。
- **告警 / 閾值通知**：本期只採集與查詢，告警另立規格。
- **WebSocket 即時推播**：Streamlit 以 polling（`st.rerun()`）取代，不走 WS。
- **Prometheus 整合**：本模組為獨立輪詢；若未來引入 Prometheus，此模組可廢棄改由 Prometheus API 提供。
- **多實例 leader lease**：首期 single-instance 部署，不實作；若未來多實例需求，參照 `monitoring.md §2.5` 加 lease。

---

## 2. 設計決策

### 2.1 為何不用 Prometheus（D1）

Prometheus 是 pull-based 採集引擎：它本身只儲存 + 查詢，仍需 exporter 提供資料。加入 Prometheus 增加一個常駐服務，而本期 Streamlit 只需「即時 + 24 小時歷史」，FastAPI 直接輪詢 exporter 即可滿足需求，無額外依賴。若未來需要長期趨勢或多服務聚合，再引入 Prometheus。

### 2.2 FastAPI background task（D2）

CPU 使用率與 IOPS 需兩次取樣的差值計算，若每次 HTTP request 才查詢 exporter，第一次永遠拿不到值，且兩次取樣間隔不固定導致數值不穩定。Background task 以固定 5 秒間隔採集，自行維護前次 CPU 快照，保證每筆數值都是穩定的 5 秒窗口。

### 2.3 Redis Sorted Set 作為時間索引滾動窗口（D3）

選用 Redis Sorted Set（score = `ts` epoch ms）取代 Redis List：

| 面向 | Redis List（舊） | Redis Sorted Set（新） |
|---|---|---|
| 保留策略 | 筆數上限（`LTRIM`） | 時間上限（`ZREMRANGEBYSCORE`） |
| 時間範圍查詢 | 不支援（須全撈再 Python 過濾） | `ZRANGEBYSCORE start end`（原生高效） |
| 回傳排序 | 由新到舊，需 `reversed()` | 天然由舊到新（score 升冪），無需反轉 |
| 24 小時資料量 | 17,280 筆全撈 ≈ 5 MB/request | 按需查詢，預設 1 小時 ≈ 720 筆 |

**寫入**（每次 `_tick()`）：

```
ZADD monitoring:infra:history <ts_ms> <json>
ZREMRANGEBYSCORE monitoring:infra:history 0 <now_ms - retention_ms>
```

- `ZADD` 的 score 為 epoch ms，value 為 `json.dumps(snapshot.model_dump())`。
- 若同一 `ts_ms` 重複寫入（sampler 重啟瞬間），後者覆蓋前者，行為符合預期。
- `ZREMRANGEBYSCORE` 以時間而非筆數清理，保證保留窗口語意正確。

**查詢**（每次 HTTP request）：

```
ZRANGEBYSCORE monitoring:infra:history <start_ms> <end_ms>
```

結果天然由舊到新（ascending score），直接對應 Streamlit `st.line_chart` 時間正序需求。

Key 命名與既有監控資料隔離：`monitoring:infra:history`（business data 用 `monitoring:stream:*`，不衝突）。

### 2.4 時間範圍查詢參數設計（D3.1）

端點接受 `start_ms` / `end_ms` 兩個可選 query parameter（均為 epoch ms 整數）：

| 情境 | start_ms | end_ms | 實際查詢範圍 |
|---|---|---|---|
| 兩者皆未提供（預設） | now - default_query_ms | now | 最近 1 小時 |
| 僅提供 start_ms | start_ms | now | start_ms 至今 |
| 僅提供 end_ms | end_ms - default_query_ms | end_ms | end_ms 前 1 小時 |
| 兩者皆提供 | start_ms | end_ms | 指定範圍 |

**驗證規則**（觸發 400 `BadRequestError`）：

- `start_ms >= end_ms`：時間範圍無效。
- `end_ms - start_ms > retention_hours * 3600 * 1000`：超出保留窗口，拒絕以防意外全撈。

超出保留窗口的舊資料已被 `ZREMRANGEBYSCORE` 清除，查詢不會出錯但回傳空陣列，此屬正常行為，不額外處理。

### 2.5 各指標計算公式（D4）

#### CPU 使用率
`node_cpu_seconds_total{mode="..."}` 為開機以來的累計秒數（單調遞增），需兩次快照差值：

```
Δidle  = Σ cpu_seconds_total{mode="idle"}  (t2) - Σ (t1)   # 所有 core 加總
Δtotal = Σ cpu_seconds_total{所有 mode}    (t2) - Σ (t1)

cpu_percent = round((1 - Δidle / Δtotal) * 100, 1)
```

- 第一次採集（無前次快照）→ `cpu_percent = null`
- `Δtotal == 0`（防除零）→ `cpu_percent = null`

#### 記憶體使用率
單次快照即可，無需差值：

```
memory_percent = round((1 - mem_available / mem_total) * 100, 1)
```

- `mem_total == 0`（防除零）→ `memory_percent = 0.0`

對應 Prometheus 指標：`node_memory_MemAvailable_bytes` / `node_memory_MemTotal_bytes`

#### 磁碟使用率
篩 `mountpoint="/"` 且 `fstype` **不屬於虛擬檔案系統**的 rootfs，單次快照即可：

```
disk_percent = round((1 - disk_avail / disk_size) * 100, 1)
```

- `disk_size == 0`（防除零）→ `disk_percent = 0.0`
- **fstype 過濾**：node-exporter 對同一 `mountpoint="/"` 可能輸出多個 series（不同 `fstype`），需排除虛擬檔案系統，只取真實儲存裝置的那筆。排除清單：`{"tmpfs", "overlay", "squashfs", "devtmpfs", "ramfs"}`；若過濾後仍有多筆，取第一筆（實務上僅剩 `ext4` / `xfs` / `btrfs` 等真實 rootfs）。
- 若過濾後無符合項目（exporter 不可用或 container 環境無 `/` rootfs）→ `disk_avail = 0, disk_size = 0` → `disk_percent = 0.0`。

對應 Prometheus 指標：`node_filesystem_avail_bytes{mountpoint="/"}` / `node_filesystem_size_bytes{mountpoint="/"}`

#### 磁碟 IOPS
`node_disk_reads_completed_total` / `node_disk_writes_completed_total` 為累計計數器，需兩次差值：

```
disk_read_iops  = round((Σ reads_total(t2)  - Σ reads_total(t1))  / interval_seconds, 2)
disk_write_iops = round((Σ writes_total(t2) - Σ writes_total(t1)) / interval_seconds, 2)
```

- 所有 device 加總（不篩 device）
- 第一次採集（無前次快照）→ `null`
- `interval_seconds == 0`（防除零）→ `null`

#### Buffer Pool 命中率
單次快照即可：

```
hit_rate = round((1 - innodb_reads / innodb_read_requests) * 100, 1)
```

- `innodb_reads`：`mysql_global_status_innodb_buffer_pool_reads`（cache miss 次數）
- `innodb_read_requests`：`mysql_global_status_innodb_buffer_pool_read_requests`（total 次數）
- `innodb_read_requests == 0`（防除零）→ `db_buffer_pool_hit_rate = null`

### 2.6 Prometheus text format 解析（D5）

exporter 回傳標準 Prometheus text format（非 JSON、非 OpenMetrics），以 `prometheus-client` 套件的 `prometheus_client.parser.text_string_to_metric_families` 解析（注意：不是 `openmetrics.parser`，後者不支援 mysqld-exporter 使用的 `untyped` 型別）。解析邏輯封裝在 `InfraProbe` 內，對外只暴露結構化 raw dict。

### 2.7 採集 best-effort（D6）

exporter 不可用（網路問題、container 未啟動）時，background task 只 log warning、跳過本次（不更新前次快照），不中斷循環，不影響業務流程。端點若 Redis 不可用則回 503，不影響其他端點。

### 2.8 httpx.AsyncClient 生命週期（D7）

`httpx.AsyncClient` 與 `InfraProbe` 皆由 lifespan 建立，再將 `InfraProbe` 注入 `InfraSampler`，與既有 `MonitoringSampler`（probe 外部注入）模式一致。`start()` 時不需要額外初始化；`stop()` 時呼叫 `await self._probe.aclose()`，由 `InfraProbe.aclose()` 負責執行 `await self._client.aclose()`——client 關閉邏輯封裝在 `InfraProbe` 內，`InfraSampler` 不需要知道 probe 的私有屬性。`InfraProbe` 單元測試以 `httpx.MockTransport` 建立假 client 並注入；`InfraSampler` 單元測試以假 probe（stub）注入。

### 2.9 lifespan 整合（D8）

`InfraSampler` 於 `app/app.py` 的 `lifespan` 函式中啟停，與既有 `WsBridge`、`MonitoringSampler` 並列，條件相同（`monitoring_infra_enabled and app_env != test`）：

```python
# app/app.py lifespan（示意，勿照抄）
infra_sampler: InfraSampler | None = None
if settings.monitoring_infra_enabled and settings.app_env != AppEnv.TEST:
    _http_client = httpx.AsyncClient()
    _probe = InfraProbe(
        settings.monitoring_infra_node_exporter_url,
        settings.monitoring_infra_mysqld_exporter_url,
        _http_client,
    )
    infra_sampler = InfraSampler(
        probe=_probe,
        redis=redis_client,
        redis_key=settings.monitoring_infra_redis_key,
        interval_seconds=settings.monitoring_infra_interval_seconds,
        retention_hours=settings.monitoring_infra_retention_hours,
    )
    await infra_sampler.start()

yield  # app running

if infra_sampler:
    await infra_sampler.stop()  # → probe.aclose() → client.aclose()
```

### 2.10 直接注入 Redis 而非 TimeSeriesStore（D10）

`InfraSampler` 直接接收 `redis: Redis`，而非透過既有的 `TimeSeriesStore` Protocol。這是刻意的偏差，原因如下：

`TimeSeriesStore` 只抽象了 Redis Stream 操作（`XADD` / `XRANGE`），不涵蓋 Sorted Set 操作（`ZADD` / `ZRANGEBYSCORE` / `ZREMRANGEBYSCORE`）。強行套用會導致 Protocol 洩漏底層細節或需要擴充 Protocol 接口，影響既有 `MonitoringSampler` 的正確性。本模組資料流路徑（Sorted Set）與既有路徑（Stream）互不干涉，直接注入 `redis: Redis` 是最簡且最清晰的實作。

> 若未來需要抽象化 Sorted Set 存取（如多後端支援），可另立 `InfraStore` Protocol，但本期不需要。

### 2.11 存取控制（D9）

`GET /admin/monitoring/infra` 使用既有 `get_current_admin`（任一 admin 皆可，`VIEWER` 以上），不額外加 `require_min_admin_role`。理由：硬體資源使用率屬運維資訊，不含業務敏感資料，且 Streamlit 即時監控頁本身已限制 admin 才能存取。

---

## 3. 資料模型

### 3.1 `InfraSnapshot`（單筆快照，存入 Redis Sorted Set）

| 欄位 | 型別 | 計算來源 | 說明 |
|---|---|---|---|
| `ts` | int（epoch ms） | `int(time.time() * 1000)` | 採樣時刻，同時作為 Sorted Set score |
| `cpu_percent` | float \| null | `node_cpu_seconds_total`（差值） | 第一次採集為 null |
| `memory_percent` | float | `node_memory_Mem*_bytes` | `1 - avail/total` |
| `disk_percent` | float | `node_filesystem_*{mountpoint="/"}` | `1 - avail/size` |
| `disk_read_iops` | float \| null | `node_disk_reads_completed_total`（差值） | 次/秒；第一次 null |
| `disk_write_iops` | float \| null | `node_disk_writes_completed_total`（差值） | 次/秒；第一次 null |
| `db_connections` | int \| null | `mysql_global_status_threads_connected` | mysqld-exporter 不可用時 null |
| `db_buffer_pool_hit_rate` | float \| null | `mysql_global_status_innodb_buffer_pool_*` | `1 - reads/read_requests`；read_requests=0 時 null |

### 3.2 `fetch_node_metrics()` 回傳 dict（raw，供計算用）

| Key | Prometheus 指標 | 說明 |
|---|---|---|
| `cpu_idle_total` | `Σ node_cpu_seconds_total{mode="idle"}` | 所有 core 加總 |
| `cpu_all_total` | `Σ node_cpu_seconds_total{所有 mode}` | 所有 core 所有 mode 加總 |
| `mem_available` | `node_memory_MemAvailable_bytes` | |
| `mem_total` | `node_memory_MemTotal_bytes` | |
| `disk_avail` | `node_filesystem_avail_bytes{mountpoint="/"}` | |
| `disk_size` | `node_filesystem_size_bytes{mountpoint="/"}` | |
| `disk_reads_total` | `Σ node_disk_reads_completed_total` | 所有 device 加總 |
| `disk_writes_total` | `Σ node_disk_writes_completed_total` | 所有 device 加總 |

### 3.3 `fetch_mysql_metrics()` 回傳 dict（raw，供計算用）

| Key | Prometheus 指標 | 說明 |
|---|---|---|
| `db_connections` | `mysql_global_status_threads_connected` | |
| `innodb_reads` | `mysql_global_status_innodb_buffer_pool_reads` | cache miss 次數 |
| `innodb_read_requests` | `mysql_global_status_innodb_buffer_pool_read_requests` | total 次數 |

### 3.4 API 查詢參數

| 參數 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `start_ms` | int（可選） | now - default_query_ms | 查詢起始時間（epoch ms，含） |
| `end_ms` | int（可選） | now | 查詢結束時間（epoch ms，含） |

預設查詢範圍由 `monitoring_infra_default_query_hours`（預設 1）決定。

### 3.5 API 回應（`GET /admin/monitoring/infra`）

**由舊到新排列**（index 0 = 最舊，index -1 = 最新），`ZRANGEBYSCORE` 天然升冪，不需反轉。Streamlit 直接轉 DataFrame 繪製折線圖：

```jsonc
{
  "snapshots": [
    {
      "ts": 1730000000000,
      "cpu_percent": null,           // 第一筆，前次快照不存在
      "memory_percent": 61.2,
      "disk_percent": 45.8,
      "disk_read_iops": null,        // 第一筆
      "disk_write_iops": null,
      "db_connections": 5,
      "db_buffer_pool_hit_rate": 98.7
    },
    {
      "ts": 1730000005000,
      "cpu_percent": 23.4,           // 第二筆起有值
      "memory_percent": 61.5,
      "disk_percent": 45.8,
      "disk_read_iops": 12.0,
      "disk_write_iops": 8.5,
      "db_connections": 6,
      "db_buffer_pool_hit_rate": 98.5
    }
    // ... 預設最多約 720 筆（1 小時 × 5 秒間隔）
  ]
}
```

---

## 4. 模組介面

### 4.1 分層放置

```
app/
├── api/routers/admin/
│   └── monitoring.py              # 新增 GET /admin/monitoring/infra 端點
├── services/monitoring/
│   ├── infra_probe.py             # InfraProbe + InfraProbeError + compute_* 純函式
│   └── infra_sampler.py           # InfraSampler：background task，寫 Redis Sorted Set
└── dtos/monitoring.py             # 新增 InfraSnapshot + InfraHistoryResponse
```

### 4.2 簽名草案

```python
# services/monitoring/infra_probe.py

class InfraProbeError(Exception):
    """exporter 不可用或回應異常時拋出。"""

class InfraProbe:
    def __init__(
        self,
        node_exporter_url: str,
        mysqld_exporter_url: str,
        client: httpx.AsyncClient,
    ) -> None: ...

    async def fetch_node_metrics(self) -> dict: ...
    # 回傳 §3.2 所定義的 raw dict；exporter 異常 → 拋 InfraProbeError
    # disk_avail / disk_size 已套用 fstype 過濾（排除 tmpfs/overlay/squashfs/devtmpfs/ramfs）

    async def fetch_mysql_metrics(self) -> dict: ...
    # 回傳 §3.3 所定義的 raw dict；exporter 異常 → 拋 InfraProbeError

    async def aclose(self) -> None: ...
    # await self._client.aclose()；由 InfraSampler.stop() 呼叫，封裝 client 生命週期

# 純函式（易測，無 I/O）
def compute_cpu_percent(prev: dict | None, curr: dict) -> float | None: ...
def compute_iops(prev: float | None, curr: float, interval: float) -> float | None: ...
def compute_memory_percent(mem_available: float, mem_total: float) -> float: ...
def compute_disk_percent(disk_avail: float, disk_size: float) -> float: ...
def compute_buffer_pool_hit_rate(
    innodb_reads: float, innodb_read_requests: float
) -> float | None: ...


# services/monitoring/infra_sampler.py

class InfraSampler:
    def __init__(
        self,
        probe: InfraProbe,
        redis: Redis,
        redis_key: str,
        interval_seconds: int,
        retention_hours: int,          # 取代舊的 max_history（筆數上限）
    ) -> None: ...

    async def start(self) -> None:
    # 建 asyncio.Task 跑 _loop()

    async def stop(self) -> None:
    # 取消 Task；await self._probe.aclose()

    async def _loop(self) -> None:
    # while True: await _tick(); await asyncio.sleep(interval_seconds)

    async def _tick(self) -> None:
    # 1. fetch_node_metrics + fetch_mysql_metrics
    # 2. compute 各指標
    # 3. 組 InfraSnapshot，json.dumps
    # 4. ZADD redis_key <ts_ms> <json>
    # 5. ZREMRANGEBYSCORE redis_key 0 <now_ms - retention_ms>
    # 6. 更新 _prev_node（前次 node raw dict）
    # probe 拋 InfraProbeError → log warning，不更新 _prev_node，不寫 Redis
```

### 4.3 Config（`app/core/config`，`monitoring_infra_` 前綴）

| 設定鍵 | 預設 | 說明 |
|---|---|---|
| `monitoring_infra_enabled` | `True` | `False` 或 `APP_ENV=test` 時不啟動 sampler |
| `monitoring_infra_node_exporter_url` | `http://node-exporter:9100` | |
| `monitoring_infra_mysqld_exporter_url` | `http://mysqld-exporter:9104` | |
| `monitoring_infra_interval_seconds` | `5` | 採集週期（秒） |
| `monitoring_infra_retention_hours` | `24` | 保留歷史時長（小時）；超出的舊資料由 `ZREMRANGEBYSCORE` 清除 |
| `monitoring_infra_default_query_hours` | `1` | 端點未帶參數時的預設查詢時間窗口（小時） |
| `monitoring_infra_redis_key` | `monitoring:infra:history` | Redis Sorted Set key |

---

## 5. 端點規格

### `GET /admin/monitoring/infra`

| 項目 | 說明 |
|---|---|
| 認證 | `Depends(get_current_admin)`（VIEWER 以上任一 admin） |
| DI | `redis: Redis = Depends(get_redis)`（沿用既有 dependency） |
| 查詢參數 | `start_ms: int | None = None`、`end_ms: int | None = None`（epoch ms） |
| 成功 | 200 + `InfraHistoryResponse`（**由舊到新**，筆數視查詢範圍而定） |
| 參數驗證失敗 | 400 `BadRequestError`（`start_ms >= end_ms` 或查詢範圍超過 `retention_hours`） |
| Redis 不可用 | 503（endpoint 以 `try/except RedisError` → `raise ServiceUnavailableError`，由全域 handler 統一回傳帶 `request_id` 的 JSON） |
| 無資料（sampler 未啟動 / 範圍內無筆） | 200 + `{"snapshots": []}` |

> **前置條件**：`app/core/exceptions/base.py` 需新增：
> - `ServiceUnavailableError(AppException)`（`status_code=503, error_code="service_unavailable"`）
> - `BadRequestError(AppException)`（`status_code=400, error_code="bad_request"`）—— 若既有體系已有則直接沿用。
>
> 兩者皆不在 router 直接 build response，由全域 handler 統一處理。

**實作骨架**：

```python
@router.get("/infra", response_model=InfraHistoryResponse)
async def get_infra(
    start_ms: int | None = Query(None, ge=0),
    end_ms: int | None = Query(None, ge=0),
    _admin: Admin = Depends(get_current_admin),
    redis: Redis = Depends(get_redis),
) -> InfraHistoryResponse:
    now_ms = int(time.time() * 1000)
    default_ms = settings.monitoring_infra_default_query_hours * 3_600_000
    retention_ms = settings.monitoring_infra_retention_hours * 3_600_000

    resolved_end = end_ms if end_ms is not None else now_ms
    resolved_start = start_ms if start_ms is not None else (resolved_end - default_ms)

    if resolved_start >= resolved_end:
        raise BadRequestError("start_ms must be less than end_ms")
    if resolved_end - resolved_start > retention_ms:
        raise BadRequestError(
            f"Query range exceeds retention window ({settings.monitoring_infra_retention_hours}h)"
        )

    try:
        raw_list = await redis.zrangebyscore(
            settings.monitoring_infra_redis_key, resolved_start, resolved_end
        )
    except RedisError as exc:
        raise ServiceUnavailableError("Redis unavailable") from exc

    # ZRANGEBYSCORE 天然由舊到新（score 升冪），無需反轉
    snapshots = [InfraSnapshot(**json.loads(item)) for item in raw_list]
    return InfraHistoryResponse(snapshots=snapshots)
```

---

## 6. TDD 測試計畫

### 6.1 `InfraProbe`（unit，注入 `httpx.MockTransport`）

- `fetch_node_metrics` 回傳含 §3.2 所有 key 的 dict，數值對應 Prometheus text。
- `fetch_node_metrics` 多 core 情況：`cpu_idle_total` = 所有 core idle 加總，`cpu_all_total` = 所有 core 所有 mode 加總。
- `fetch_node_metrics` 磁碟 fstype 過濾：Prometheus text 同時含 `mountpoint="/"` 的 `tmpfs` 與 `ext4` series → `disk_avail` / `disk_size` 取 `ext4` 那筆（虛擬 fs 被排除）。
- `fetch_node_metrics` 磁碟 fstype 全為虛擬 fs（如只有 `tmpfs`）→ `disk_avail = 0, disk_size = 0`。
- `fetch_mysql_metrics` 回傳含 §3.3 所有 key 的 dict。
- exporter 回 5xx → 拋 `InfraProbeError`。
- exporter 連線失敗（`httpx.TransportError`）→ 拋 `InfraProbeError`。
- `aclose()` 呼叫後 underlying `httpx.AsyncClient` 已關閉（驗 `client.is_closed`）。

### 6.2 計算純函式（unit）

**`compute_cpu_percent`**
- 正常差值：`Δidle=100, Δtotal=200` → `50.0`
- `prev=None` → `None`
- `Δtotal=0` → `None`
- 全 idle（`Δidle=Δtotal`）→ `0.0`

**`compute_iops`**
- 正常：`(curr-prev) / interval` → 正確 IOPS
- `prev=None` → `None`
- `interval=0` → `None`

**`compute_memory_percent`**
- 正常：`1 - avail/total` → 正確百分比
- `mem_total=0` → `0.0`

**`compute_disk_percent`**
- 正常：`1 - avail/size` → 正確百分比
- `disk_size=0` → `0.0`

**`compute_buffer_pool_hit_rate`**
- 正常：`1 - reads/read_requests` → 正確百分比
- `read_requests=0` → `None`

### 6.3 `InfraSampler`（unit，fakeredis + 假 probe）

- `_tick()` 一次 → Redis Sorted Set 有 1 筆（`ZCARD == 1`），反序列化後結構符合 `InfraSnapshot`，score 對應 `ts`。
- `_tick()` 寫入的 score 等於快照的 `ts` 欄位（`ZSCORE redis_key member == snapshot.ts`）。
- `ZREMRANGEBYSCORE` 生效：建立一筆 `ts = now - (retention_hours + 1) * 3600 * 1000` 的舊資料後執行 `_tick()`，舊資料應被清除（`ZCARD` 減少）。
- probe 拋 `InfraProbeError` → 只 log warning，Redis Sorted Set 不新增（`ZCARD` 不變），循環不中斷（連續 `_tick()` 可繼續）。
- 連續兩次 `_tick()`（probe 正常）→ 第二筆的 `cpu_percent` 不為 null（前次快照存在）。
- 第一次 `_tick()` → 第一筆的 `cpu_percent` 為 null、`disk_read_iops` 為 null。

### 6.4 `GET /admin/monitoring/infra`（integration）✅

四種時間範圍情境（對應 §2.4）全部覆蓋：

- 非 admin（無 token）→ 401。
- Redis Sorted Set 有 3 筆（ts 分別為 t1 < t2 < t3）→ 200，`snapshots` 長度 3，且 `ts` 由小到大（由舊到新）。
- Redis Sorted Set 為空 → 200 + `{"snapshots": []}`.
- Redis 不可用 → 503。
- `start_ms >= end_ms` → 400。
- `end_ms - start_ms > retention_hours * 3_600_000` → 400。
- 兩者皆提供（`start_ms` + `end_ms`）：只有 ts 落在 `[start_ms, end_ms]` 範圍內的筆回傳。
- 僅帶 `start_ms`，不帶 `end_ms`：回傳 `[start_ms, now]` 的資料（不報錯）。
- 僅帶 `end_ms`，不帶 `start_ms`：查詢範圍為 `[end_ms - default_ms, end_ms]`，超出下界與上界的筆均不回傳（不報錯）。
- 兩者皆不帶：回傳預設 1 小時範圍（`[now - default_ms, now]`）的資料（不報錯）。

---

## 7. 實作順序（TDD 里程碑）

1. `dtos/monitoring.py` 新增 `InfraSnapshot` + `InfraHistoryResponse`（unit 測試：DTO 欄位驗證）。
2. `monitoring_infra_*` config 欄位進 `BaseAppSettings`（unit 測試：預設值正確；含 `retention_hours=24`、`default_query_hours=1`）。
3. `InfraProbe` + `InfraProbeError`（unit 測試：§6.1）。
4. 五個計算純函式（unit 測試：§6.2）。
5. `InfraSampler`（unit 測試：§6.3，fakeredis + 假 probe；驗 ZADD / ZREMRANGEBYSCORE 語意）。
6. `app/app.py` lifespan 掛載 `InfraSampler`（lifespan 建立 `httpx.AsyncClient` + `InfraProbe`，條件：`monitoring_infra_enabled and app_env != test`）。
7. `app/core/exceptions/base.py` 新增 `ServiceUnavailableError`（`status_code=503, error_code="service_unavailable"`）。**`BadRequestError`（400）已存在於既有例外體系（已驗證），直接沿用，不重複新增。** unit 測試只驗 `ServiceUnavailableError` 的 `status_code` 與 `error_code`。
8. `GET /admin/monitoring/infra` 端點（integration 測試：§6.4，含時間範圍查詢與驗證錯誤案例）。
9. 提交前檢查全綠（`ruff check` / `ruff format --check` / `pyright` / `pytest`）。
10. 真 node-exporter + mysqld-exporter 煙霧測試（手動驗證 compose 啟動後端點回傳非空，含帶 / 不帶查詢參數兩種呼叫）。

---

## 8. 已定案決策摘要

| 項目 | 決策 |
|---|---|
| 存取控制 | `get_current_admin`（VIEWER 以上），不加額外 role 限制 |
| 多實例 | 首期 single-instance，不實作 leader lease |
| 磁碟掛載點 | 固定篩 `mountpoint="/"` + 排除虛擬 fstype（`tmpfs/overlay/squashfs/devtmpfs/ramfs`），取第一筆真實 rootfs |
| mysqld_exporter 帳號 | 沿用 app 帳號；正式環境建議改唯讀監控帳號（infra 設定，非本模組範圍） |
| Redis 資料結構 | **Sorted Set**（score = epoch ms）；取代舊方案的 List，支援原生時間範圍查詢 |
| 保留策略 | **時間上限**（`ZREMRANGEBYSCORE` 刪 24 小時前資料）；取代舊方案的筆數上限（`LTRIM`） |
| Redis 序列化 | `json.dumps(snapshot.model_dump())`；讀取 `json.loads` |
| 回傳排序 | **由舊到新**（`ZRANGEBYSCORE` 天然升冪，無需 `reversed()`） |
| 時間範圍查詢 | `?start_ms=&end_ms=`（均選填，epoch ms）；預設查詢最近 1 小時；超出保留窗口拒絕（400） |
| httpx client | lifespan 建立 `httpx.AsyncClient` 並傳入 `InfraProbe`，`InfraProbe` 再注入 `InfraSampler`；`stop()` 呼叫 `probe.aclose()` → `client.aclose()`（封裝清理，不洩漏私有屬性） |
| Redis 503 | endpoint 以 `try/except RedisError` → `raise ServiceUnavailableError`；由全域 handler 統一格式化，帶 `request_id` |
| 計算公式防除零 | `Δtotal=0` → cpu null；`mem_total=0` → 0.0；`disk_size=0` → 0.0；`read_requests=0` → null |
| parser | `prometheus_client.parser.text_string_to_metric_families`（非 openmetrics） |
