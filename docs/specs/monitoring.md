# 規格書：Monitoring 模組（Admin 監控：日誌查詢／DB 狀態／歷史查詢）

> 🔺 **變更註記（IntEnum，權威 delta）**——依 [`enum-int.md`](./enum-int.md)：`ADMIN_ROLE_RANK` dict **已移除**（rank = enum 值，`require_min_admin_role` 直接比值）；本文「依賴 `ADMIN_ROLE_RANK`」改讀「`require_min_admin_role`（rank=value）」。端點授權（`require_min_admin_role(SUPER_ADMIN)` 等）**行為不變**（`AdminRole` 改 IntEnum、root=ROOT 自動涵蓋 super_admin 門檻）。

> 狀態：**已實作（✅ 547 tests 全綠，ruff / pyright 通過）** ／ 目標版本：next+4 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> **語言**：繁體中文。
>
> 🔗 依賴既有機制：WS 骨架（[`websocket.md`](./websocket.md)：`Publisher.to_topic`／`event` 封套／`TOPIC_MIN_ROLE` 授權掛勾／lifespan 背景 task 慣例）、認證授權（`get_current_admin`／`require_min_admin_role`、`ADMIN_ROLE_RANK`，[`rbac.md`](./rbac.md)）、Redis（`app/core/redis`，測試以 `fakeredis`）、長連線短命 session（`get_session_factory` → `AsyncSessionLocal`，websocket §2.2/§4）、結構化 logging（`app/core/logging.py`：`_RequestIdFilter`／`_EmailMaskFilter`）。
>
> ⚠️ **範圍**：本規格定義 admin 監控的**三項能力共用的一套骨架**——(1) 系統日誌查詢、(2) 資料庫狀態監控、(3) 即時資料的歷史查詢。三者本質同構：**樣本/事件 → 落地時序 Store（Redis Stream）→ 即時 WS 推播 → HTTP 歷史查詢**。本文定義該骨架、三項的具體採集/查詢，與可抽換的 Store 介面（未來接外部聚合器/TSDB）。
>
> 🧩 **前置依賴（本模組實作前需先落地）**：WS 基礎模組（`Publisher`、`event` 封套、`topics.TOPIC_MIN_ROLE`、lifespan 背景 task）已於 [`websocket.md`](./websocket.md) 定案並實作。本模組**正是 websocket §10 Open Q #1/#3 所延後的「具體業務 topic」與「持久化/replay」**的落地。

---

## 0. 功能總覽（先讀這裡）

**一句話**：讓 admin 後台能**查系統日誌**、**看 DB 即時/歷史狀態**、並對**即時推播的資料回放歷史**；三者共用「時序 Store（Redis Stream）+ 即時 WS 推播 + HTTP 查詢」一套骨架，Store 藏在介面後可換外部聚合器。

**三項能力對應的骨架切面**：

| 能力 | 採集來源 | 即時（push→WS topic） | 歷史/查詢（pull→HTTP） | Store |
|---|---|---|---|---|
| 系統日誌查詢 | app logging（非阻塞 handler） | `monitor.logs`（可選 tail） | `GET /monitoring/logs`（篩選+游標分頁） | `monitor:stream:logs` |
| 資料庫狀態監控 | MariaDB status/PROCESSLIST + SQLAlchemy pool（sampler 週期採） | `monitor.db`（sampler 推） | `GET /monitoring/db`（當下快照）、`GET /monitoring/db/history`（股價式折線圖，ZADD Sorted Set） | `monitor:stream:db`（Stream）＋ `monitoring:db:history`（Sorted Set） |
| 即時資料歷史查詢 | 上述 sampler 樣本（同一份） | 重用上面兩個 topic | `GET /monitoring/metrics/{name}`（range 查詢） | `monitor:stream:{name}` |

**四個定案前提（見 §2）**：

| 維度 | 定案 | 影響 |
|---|---|---|
| 時序落地 | **Redis Stream**（已定案，websocket §10 #3/#4）為統一 hot store | 可回放（`XRANGE`）、有序 ID、有界（`MAXLEN`）；非長期歸檔 |
| 後端可換 | **Store 介面（Protocol）**，interim=Redis Stream，future=CloudWatch/ELK/TSDB adapter | Service/API/router 對後端零耦合；可丟棄式 interim |
| 即時管道 | **重用 WS `Publisher.to_topic`**，不新建通道 | 監控只是新業務 topic（`monitor.*`），骨架不動 |
| 非關鍵路徑 | **監控全程 best-effort**，永不阻塞/拖垮主流程 | 日誌 handler 佇列滿即丟、sampler/Store 失敗只 log |

**端點（全走 `get_current_admin` + topic/端點級 `require_min_admin_role`）**：
`GET /monitoring/logs`、`GET /monitoring/db`、`GET /monitoring/db/history`、`GET /monitoring/metrics/{name}`。

> 🔗 **跨規格端點**：`GET /monitoring/infra`（OS 硬體層 + MariaDB 引擎層指標，Sorted Set 歷史查詢）定義於 [`infra-monitoring.md`](./infra-monitoring.md)，與本模組共用同一 router 檔案（`app/api/routers/monitoring/router.py`）但屬不同採集骨架（InfraSampler 輪詢 exporter，非 MonitoringSampler）。`GET /health/node-exporter`、`GET /health/mysqld-exporter`（exporter 可達性 health check）定義於 [`infra-monitoring.md §5`](./infra-monitoring.md)。

---

## 1. 背景與目標

CMS admin 需要可觀測性：查最近系統日誌以排錯、看 DB 連線/負載即時狀態、對即時看板的數據回放歷史趨勢。既有 logging 只寫 stdout（多實例下無法集中查詢）；WS 為 at-most-once、不落地（無歷史）。本模組補上「可查詢的時序層」與「採集/推播/查詢」的分層骨架。

### 目標

- 定義 **統一時序骨架**：`sample/event → Store（Redis Stream）→ 即時 WS 推播 → HTTP 查詢`，三項能力共用。
- **可抽換 Store 介面**：interim 用 Redis Stream；預留 `CloudWatchLogStore`／`ELKLogStore`／TSDB adapter，**未來接外部聚合器只換 adapter、上層零改**。
- **系統日誌查詢**：非阻塞 logging handler 擷取 → 背景 flush 進 Store → 篩選/游標分頁查詢；沿用既有遮罩 filter。
- **DB 狀態監控**：唯讀短命 session 讀 MariaDB `SHOW GLOBAL STATUS`／`information_schema.PROCESSLIST` + pool 指標；快照端點 + sampler 週期採樣即時推播。
- **即時資料歷史查詢**：sampler 樣本同時落地與推播；range 查詢回放（websocket §10 延後的 replay）。
- **多實例正確性**：sampler 以 Redis leader lease 選單一實例採樣，避免 N 倍寫入/重複推播。
- 分層對齊：**API（router）→ Service → Store/Probe → Redis/DB**；監控觸發（sampler、log handler）為背景元件，於 lifespan 啟停（比照 `WsBridge`）。
- **嚴格 TDD**：每個行為先寫失敗測試（`fakeredis` streams、`httpx` client、假 Probe/假時鐘）。

### 非目標（Out of scope）

- **告警/閾值通知（alerting）**：本期只採集/查詢/推播；閾值告警、通知管道另立規格。
- **長期歸檔/資料湖**：Store 為有界 hot buffer；歸檔（Stream → S3/aggregator）與長保留另議（見 §10）。
- **APM/分散式追蹤（trace/span）**：本期不做 tracing；`request_id` 關聯已足夠。
- **業務指標（monitor 以外）**：本期只做 logs/db 兩類採集源；其他業務時序指標沿用同骨架另立（見 §10）。
- **跨服務基礎設施監控**（機器 CPU/記憶體、外部服務健康）：屬 infra 監控（Prometheus node-exporter 等），非本 app 範圍。

---

## 2. 設計決策

### 2.1 統一時序骨架：三項能力同構，共用一套 Store + 推播 + 查詢（D1，核心）

三項能力表面不同，本質是同一資料流：

```
採集源 ──▶ TimeSeriesStore.append(stream, entry)     ← 落地（有界、可回放）
   │                                    │
   │                                    └──▶ HTTP 查詢：Store.query(stream, range, cursor)
   └──▶ Publisher.to_topic("monitor.X", event)        ← 即時（at-most-once, best-effort）
```

- **落地與即時是兩條獨立管道**（Store 為權威可回放、WS 為即時 best-effort）；前端標準用法＝**開頁先 `GET` 拉最近 N 筆歷史 → 再 `subscribe` WS 接續即時**，天然補齊 WS 的 at-most-once gap（不需 WS 內建 replay）。
- 骨架不綁定「監控」語意：`TimeSeriesStore` 對任意 `(stream, entry)` 運作，故未來新增業務時序指標零改骨架（§10）。

### 2.2 Redis Stream 作為統一 hot store，藏在可抽換介面後（D2）

- **為何 Stream 不用 pub/sub**：pub/sub 是 at-most-once、無回放（websocket §2.4 即時推播用）；**歷史查詢需要有序、可回放、可範圍掃描** → Redis Stream（`XADD`/`XRANGE`/`XLEN`/`XTRIM`）正是為此（對齊 websocket §10 #3「若需 replay 要用 Redis Stream 而非 pub/sub」）。
- **有界 hot buffer，非長期歸檔**：每 stream 設 `MAXLEN ~ N`（近似修剪，`~` 讓 Redis 批次刪除更省）；可選 `MINID` 按時間修剪（保留近 `retention_seconds`）。逾量最舊者淘汰——監控是 best-effort，不保證無限歷史。
- **Store 介面（Protocol）解耦後端**：
  ```python
  class TimeSeriesStore(Protocol):
      async def append(self, stream: str, entry: dict, *, maxlen: int | None) -> str: ...         # 回 entry id
      async def append_many(self, stream: str, entries: list[dict], *, maxlen: int | None) -> list[str]: ...  # pipeline 批次，回 id list
      async def query(self, stream: str, *, since: int | None, until: int | None,
                      cursor: str | None, limit: int) -> Page: ...                               # 時間/ID 區間 + 游標
  ```
  - interim：`RedisStreamStore`（`XADD MAXLEN ~ / XRANGE`）。
  - future：`CloudWatchLogStore`／`ELKLogStore`／`InfluxStore`——**同介面、換 adapter**，`LogQueryService`/`MetricQueryService`/router **零改動**。這是「可丟棄的 interim」該有的設計（滿足使用者「未來能接外部聚合器」的硬需求）。
- **游標＝Stream entry ID**（`<ms>-<seq>`，天生單調、無 offset 漂移）；分頁穩定、修剪不影響已持有游標語意（超出保留窗回空頁）。

### 2.3 日誌擷取：非阻塞 handler → 有界佇列 → 背景 flush（D3）

- 日誌由既有 `app/core/logging.py` 產生（同步情境）；Redis 為 async → **handler 內不可 `await`**。
- **`RedisStreamLogHandler`（`logging.Handler`）**：`emit()` 只把格式化後的 `LogEntry` **`put_nowait` 進記憶體有界佇列**（fire-and-forget）；**佇列滿 → 丟棄最舊/直接 drop（不阻塞、不拋例外）**——比照 WS 背壓哲學（監控寧可掉 log，不可拖垮請求）。
- **背景 flush task**（lifespan 啟動）：每 `flush_interval` 或滿 `flush_batch_size` → 以 `store.append_many()` 一次 Redis pipeline 批次寫入 `monitor:stream:logs`（`transaction=False`，N 筆 → 1 round-trip）；失敗整批 log warning、佇列繼續（best-effort、all-or-nothing 與 best-effort 語意一致）。
- **遮罩前置**：沿用既有 `_RequestIdFilter`（注入 `request_id`）與 `_EmailMaskFilter`（遮 email）——**入 Store 前已遮罩**，Store/查詢/推播都不含明文敏感（§6）。
- **掛載點**：`setup_logging()` 於非測試環境**額外加**此 handler（與既有 `StreamHandler` 並存）；`APP_ENV=test` **不啟用**（避免污染測試、且測試無真 Redis）。啟用與否走 `monitoring_enabled` config。
- **可選即時 tail**：flush 時對 `monitor.logs` topic 同步 `Publisher.to_topic`（訂閱者才收；預設關，避免高頻 log 灌爆 WS，走 config）。⚠️ **尚未實作**：config key `monitoring_log_push_enabled` 已存在並預設 `False`，但 `run_log_flusher` 目前不含對 `monitor.logs` 推播的邏輯，待未來版本補齊。

### 2.4 DB 狀態監控：唯讀短命 session，讀 MariaDB status/PROCESSLIST + pool（D4）

> 本專案正式 DB 為 **MariaDB**（`mysql+asyncmy`，見 `app/core/config`）；測試用 SQLite。以下查詢為 **MariaDB/MySQL 專屬**，非 Postgres。

**設計原則：ORM 可攜性邊界（呼應「用 ORM 就是為了容易換 DB」）**

ORM 抽象的是**應用資料表的存取**（models／repositories／CRUD）——這一層本專案所有規格保持可攜（SQLite 測試 + MariaDB 正式雙跑），本模組亦不破壞。但 **DB 伺服器自身的狀態 introspection**（連線數、replication、DB 大小）**沒有 ANSI／ORM 可攜等價物**（`SHOW GLOBAL STATUS` vs `pg_stat_*` 各家不同；SQLite 根本無「伺服器」概念），**不在 ORM 抽象合約內**。故把可攜與不可攜**切乾淨**、可攜性維持在**模組邊界**（與 §2.2 `TimeSeriesStore` 同一手法）：

- **可攜基準（always-on）**：`PoolStatsProbe` 只讀 **SQLAlchemy `engine.pool`**（純 Python、無 raw SQL）→ **任何後端**都給 pool 指標，換 DB **零改**。這是 feature 的保底層。
- **引擎專屬擴充（可插拔）**：`MariaDbStatsProbe` 等隱藏在 `DbStatsProbe` 介面後，依 `db_dialect` **自動選用**；換 DB ＝**新增一支對應 probe**（如未來 `PostgresDbStatsProbe`），`DbStatsService`／API／DTO／前端**零改**。無對應 probe → **自動退化只回 pool 指標**（feature 不壞、不報錯）。
- 換言之：**把不可攜關進介面，正是 ORM 精神的延伸而非違背**——ORM 讓資料存取可攜，`DbStatsProbe` 讓伺服器監控在模組邊界可攜。（若要「深度 DB 指標且完全零 app 端方言」，正解是外部 exporter（如 mysqld_exporter）+ Prometheus，屬 infra 監控、見 §1 非目標。）

- **指標（首期）**：
  - SQLAlchemy 連線池：`pool.size()`／`checkedout()`／`overflow()`／`checkedin()`（不需 DB round-trip）。
  - 執行緒/連線（`SHOW GLOBAL STATUS`）：`Threads_connected`、`Threads_running`、`Max_used_connections`、`Aborted_connects`；上限 `SHOW VARIABLES LIKE 'max_connections'`。
  - 連線狀態聚合（`information_schema.PROCESSLIST`）：依 `COMMAND`/`STATE` 聚合（`Sleep`≈idle、`Query`≈active）、最長執行中查詢 `MAX(TIME)`。
  - DB 大小（`information_schema.TABLES`）：`SUM(data_length + index_length) WHERE table_schema = <current db>`。
  - （可選，見 §10）：slow query（`Slow_queries` 狀態量或 `performance_schema.events_statements_summary_by_digest`）、InnoDB buffer pool／row lock（`SHOW ENGINE INNODB STATUS`／`information_schema.INNODB_METRICS`）、replication lag（`SHOW REPLICA STATUS` → `Seconds_Behind_Source`）。
- **權限注意（監控帳號授權）**：`information_schema.PROCESSLIST` 預設只顯示自己的連線，除非帳號具 **`PROCESS`** 權限；`SHOW REPLICA STATUS` 需 **`REPLICATION CLIENT`／`BINLOG MONITOR`**。監控 DB user 應僅授予這些**唯讀**權限（最小權限）。
- **短命 session**：透過 `get_session_factory`（websocket §2.2/§4）每次採樣開短命 session、用畢即還——**不綁 request scope、不與背景 task 共用單一 `AsyncSession`**。
- **probe 選用（依 `db_dialect` 自動）**：`get_db_stats_service` 依當前 dialect 組裝——`mysql*` → `MariaDbStatsProbe`（讀 `SHOW GLOBAL STATUS`／`information_schema.PROCESSLIST`／`TABLES`）；其餘（含 SQLite 測試）→ 只掛 `PoolStatsProbe`。DB 專屬區塊在無對應 probe 時標 `unsupported`；測試可注入假 probe。**監控不得因後端差異在測試/其他 DB 上爆炸**。

### 2.5 背景採樣器（Sampler）：lifespan 啟停 + 多實例 leader lease（D5）

- **`MonitoringSampler`**：每 `db_sample_interval`（預設 5 秒）採一次 DB 狀態 → **同一份樣本三路輸出**：
  1. `Store.append("monitor:stream:db", sample)` — 落地至 Redis Stream（`XADD MAXLEN ~`，游標分頁查詢 / log-viewer 模式）。
  2. `Publisher.to_topic("monitor.db", event(sample))` — 即時 WS 推播（at-most-once，前端訂閱用）。
  3. `redis.zadd(monitoring_db_sorted_set_key, {json.dumps(sample): ts_ms})` + `redis.zremrangebyscore(…, 0, ts_ms - retention_ms)` — 落地至 **Redis Sorted Set**（score＝epoch ms）供 `GET /monitoring/db/history` 時間區間查詢（股價式折線圖）；同步以 `ZREMRANGEBYSCORE` 執行時間保留（預設 24h），無需 `MAXLEN`。
- **為何雙寫（Stream ＋ Sorted Set）**：Stream 適合游標翻頁 / WebSocket tail；Sorted Set 的 `ZRANGEBYSCORE start_ms end_ms` 天然支援股價式「先拉近 N 分鐘歷史 → 每 5 秒增量 append」，無 offset 漂移問題。兩者職責不同，共用同一份採樣資料。
- **多實例正確性（關鍵最佳實踐）**：多實例下若每個實例都採樣 → N 倍寫入 + 重複推播。以 **Redis leader lease** 選單一採樣者：
  - `SET monitor:sampler:leader <instance_id> NX EX <lease>`；搶到者為 leader、才採樣，並週期續租；未搶到者待命（leader 掛掉 lease 過期後他人接手）。
  - `lease > sample_interval`（建議 `≥ 2×`），避免續租邊界抖動造成雙採。
- **lifespan 啟停**（比照 `WsBridge`，見 websocket §2.10）：`create_app`/lifespan startup 建 sampler + log-flush task 並 `start()`；shutdown `stop()`（取消 task、釋放 leader lease）。
- **best-effort**：採樣/推播/落地任一失敗只 log，不中斷循環、不影響業務。

### 2.6 即時推播：重用 WS topic，不新建通道（D6）

- 推播沿用 websocket `event` 封套：`{"type":"event","topic":"monitor.db","ts":<epoch>,"data":{...}}`。
- **topic 授權**（websocket §2.9 掛勾）：於 `app/services/ws/topics.py` 填 `TOPIC_MIN_ROLE`：
  - `monitor.db` → 建議 `VIEWER`（任一 admin 可看 DB 健康）。
  - `monitor.logs` → 建議 `SUPER_ADMIN`（日誌可能含敏感上下文）。
  - 實際門檻屬業務裁量，值可調（本文給預設建議）。
- 即時 = at-most-once（實例當下無訂閱者即丟）；歷史完整性由 Store 保證（§2.1 前端補齊模式）。

### 2.7 查詢 API：游標分頁、時間區間 + app 端篩選（D7）

- **日誌**：`GET /monitoring/logs?level=&since=&until=&request_id=&logger=&cursor=&limit=`
  - `since`/`until`＝epoch ms（映射 Stream ID 區間）；`cursor`＝上一頁末筆 entry ID（`XRANGE (cursor +`）。
  - `level`/`request_id`/`logger` 為 app 端過濾（在**有界掃描窗**內；Redis Stream 無二級索引）。若某維度查詢極頻繁 → 未來加 per-level 分流 stream 或次級索引（先不過度設計）。
- **DB 快照**：`GET /monitoring/db` → 即時採一次（或回最近一筆 stream entry）回當下狀態。
- **DB 歷史（股價式折線圖）**：`GET /monitoring/db/history?start_ms=&end_ms=`
  - 讀 Sorted Set（`ZRANGEBYSCORE monitoring:db:history start_ms end_ms`）；結果以 score 升冪（oldest-first）。
  - 時間參數語意與 `/infra` 完全一致（4 種情境）：
    1. 帶 `start_ms` + `end_ms` → 查 `[start_ms, end_ms]`。
    2. 只帶 `start_ms` → 查 `[start_ms, now]`。
    3. 只帶 `end_ms` → 查 `[end_ms - default_ms, end_ms]`（`default_ms = monitoring_infra_default_query_hours × 3600000`）。
    4. 無參數 → 查 `[now - default_ms, now]`（即最近 N 小時）。
  - 驗證：`start_ms >= end_ms` → 400；查詢範圍超出 `monitoring_db_retention_hours` → 400；Redis 不可用 → 503。
  - 回 `DbHistoryResponse(snapshots: list[DbSample])`（不分頁，全量回傳有界區間）。
- **歷史指標**：`GET /monitoring/metrics/{name}?since=&until=&cursor=&limit=`（`name` ∈ 允許集合，如 `db`）→ range 回放。
- **上限**：`limit ≤ monitoring_query_max_limit`（防重負載）；回應含 `next_cursor`（無更多則 null）。

### 2.8 分層與模組放置（D8）

```
app/
├── api/routers/monitoring/
│   └── router.py                # GET logs / db / metrics（認證 + require_min_admin_role）
├── services/monitoring/
│   ├── store.py                 # TimeSeriesStore 介面 + RedisStreamStore（future: Cloud/ELK/TSDB adapter）
│   ├── log_handler.py           # RedisStreamLogHandler（非阻塞）+ 背景 flush task
│   ├── logs.py                  # LogQueryService（查詢/篩選/分頁）
│   ├── db_probe.py              # DbStatsProbe 介面 + MariaDb/Pool/退化 probe
│   ├── db_stats.py              # DbStatsService（快照）
│   ├── metrics.py               # MetricQueryService（歷史 range 查詢）
│   └── sampler.py               # MonitoringSampler（leader lease + 週期採樣 → 落地 + 推播）
├── dtos/monitoring.py           # LogEntry / DbSample / MetricPage 等 Pydantic DTO（跨層）
└── core/config/base.py          # monitoring_* 設定（§4.1）
```
- **API 層**只做認證/授權、參數驗證、委派；採集（sampler、log handler）為背景元件、於 lifespan 啟停。
- topic 註冊於既有 `app/services/ws/topics.py`（不新增 WS 檔案）。

### 2.9 保留策略與資源上限（防 DoS / 記憶體膨脹）（D9）

- 每 stream `MAXLEN ~ monitoring_*_stream_maxlen`（近似修剪）；可選 `retention_seconds` 以 `MINID` 按時間修剪。
- log handler 記憶體佇列 `maxsize`（滿即丟最舊）；flush 批次上限。
- 查詢 `limit` 上限、掃描窗上限。
- 全部走 config（§4.1），預設保守、實際值待壓測（§10）。

### 2.10 監控自身為非關鍵路徑（best-effort 貫穿）（D10）

- 日誌 handler 佇列滿 → 丟棄（不阻塞請求）；flush/append/publish/採樣失敗 → 只 log warning、循環續行。
- 查詢端點若 Store 不可用 → 回 5xx（由既有全域 handler 帶 `request_id`），**不影響其他業務端點**。
- 監控元件的例外**絕不可**冒泡到業務請求路徑。

---

## 3. 資料模型與協定

### 3.1 `LogEntry`（落 `monitor:stream:logs` 的欄位；已遮罩）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ts` | int（epoch ms） | 記錄時刻（＝stream ID 高位，查詢 `since/until` 用） |
| `level` | str | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `logger` | str | logger 名稱（`record.name`） |
| `message` | str | 已過 `_EmailMaskFilter` 遮罩的訊息 |
| `request_id` | str \| null | 來自 `_RequestIdFilter`（關聯請求） |
| `module`/`func`/`line` | str/int | 來源位置（可選） |

### 3.2 `DbSample`（落 `monitor:stream:db` 的欄位）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ts` | int（epoch ms） | 採樣時刻 |
| `pool` | obj | `{size, checked_out, overflow, checked_in}`（SQLAlchemy pool） |
| `connections` | obj | `{connected, running, idle}`（`Threads_connected`/`Threads_running` + `PROCESSLIST` 聚合） |
| `db_size_bytes` | int \| null | `information_schema.TABLES` 加總（非 MariaDB → null） |
| `longest_query_seconds` | float \| null | 最長執行中查詢時長（`MAX(TIME)`，可選） |
| `backend` | str | `mariadb`（MariaDbStatsProbe）／`pool_only`（PoolStatsProbe，SQLite 或非 MariaDB 退化）（probe 能力標示） |

### 3.3 `DbHistoryResponse`（`GET /monitoring/db/history` 回應）

```python
class DbHistoryResponse(BaseModel):
    snapshots: list[DbSample]   # 以 ts 升冪（oldest-first）
```

### 3.4 查詢回應封套（游標分頁）

```jsonc
// GET /monitoring/logs?level=ERROR&limit=50
{
  "items": [ { "ts": 1730000000123, "level": "ERROR", "logger": "app.services.auth",
               "message": "Login failed ...", "request_id": "req-abc" } ],
  "next_cursor": "1730000000123-0"   // null = 無更多
}
```

### 3.5 WS 即時封套（重用 websocket §3.3 `event`）

```jsonc
{ "type": "event", "topic": "monitor.db", "ts": 1730000000,
  "data": { "pool": { "checked_out": 3, "size": 10 },
            "connections": { "running": 2, "idle": 8 } } }
```

---

## 4. 模組介面（簽名草案）

> 皆 async；實際簽名以實作時 TDD 收斂為準。

```python
# services/monitoring/store.py
class TimeSeriesStore(Protocol):
    async def append(self, stream: str, entry: dict, *, maxlen: int | None = None) -> str: ...
    async def append_many(self, stream: str, entries: list[dict], *, maxlen: int | None = None) -> list[str]: ...
    # RedisStreamStore 以 pipeline(transaction=False) 實作：N 筆 XADD → 1 round-trip
    async def query(self, stream: str, *, since: int | None = None, until: int | None = None,
                    cursor: str | None = None, limit: int = 100) -> "Page": ...

class RedisStreamStore:                       # interim（XADD MAXLEN ~ / XRANGE）
    def __init__(self, client: redis.Redis) -> None: ...
# future: CloudWatchLogStore / ELKLogStore / InfluxStore —— 同介面

# services/monitoring/log_handler.py
class RedisStreamLogHandler(logging.Handler):
    """emit() 只 put_nowait 進有界佇列（fire-and-forget、滿即丟、不阻塞、不拋）。"""
    def __init__(self, queue: asyncio.Queue[dict]) -> None: ...
async def run_log_flusher(queue, store, *, interval, batch_size, maxlen) -> None: ...   # lifespan task

# services/monitoring/db_probe.py
class DbStatsProbe(Protocol):
    async def sample(self) -> dict: ...        # 回 DbSample dict（能力不足欄位給 null/unsupported）
class MariaDbStatsProbe: ...                   # 讀 SHOW GLOBAL STATUS / information_schema（短命 session）
class PoolStatsProbe: ...                      # 只讀 engine pool（DB 無關；退化用）

# services/monitoring/logs.py
class LogQueryService:
    def __init__(self, store: TimeSeriesStore) -> None: ...
    async def query(self, *, level, since, until, request_id, logger, cursor, limit) -> "Page": ...

# services/monitoring/db_stats.py
class DbStatsService:
    def __init__(self, probe: DbStatsProbe) -> None: ...
    async def snapshot(self) -> dict: ...

# services/monitoring/metrics.py
class MetricQueryService:
    def __init__(self, store: TimeSeriesStore) -> None: ...
    async def range(self, name: str, *, since, until, cursor, limit) -> "Page": ...

# services/monitoring/sampler.py — lifespan 啟停、多實例 leader lease
class MonitoringSampler:
    def __init__(
        self,
        client: redis.Redis,
        probe: DbStatsProbe,
        store: TimeSeriesStore,
        publisher: Publisher,
        *,
        stream: str,
        maxlen: int,
        sample_interval: float,
        lease_seconds: int,
        instance_id: str | None = None,
        sorted_set_key: str | None = None,   # 股價式折線圖 Sorted Set；None 表示不寫
        retention_hours: int = 24,           # Sorted Set 時間保留窗（ZREMRANGEBYSCORE）
    ) -> None: ...
    async def start(self) -> None: ...        # 建背景 task
    async def stop(self) -> None: ...         # 取消 task + 釋放 leader lease
```

- **DI／lifespan**：`RedisStreamStore`/`Publisher` 由 Redis client 建；`MonitoringSampler` 與 log-flush task 於 lifespan startup 啟、shutdown 停（比照 `WsBridge`，websocket §2.10）。sampler 持 `get_session_factory` 開短命 session（§2.4）。
- **provider（`api/dependencies/services.py`，比照 `get_*_service`）**：`get_time_series_store`、`get_log_query_service`、`get_db_stats_service`、`get_metric_query_service`。
- **端點授權**：`GET /monitoring/*` 以 `Depends(get_current_admin)` + 視敏感度加 `Depends(require_min_admin_role(...))`（logs 建議 `SUPER_ADMIN`）。
- **`GET /monitoring/db/history`**：直接 `Depends(get_redis)`（無需 service layer，僅讀 Sorted Set）；回 `DbHistoryResponse(snapshots=…)`。

### 4.1 設定（Config，`app/core/config`）

所有參數走 config、統一 `monitoring_` 前綴（時間一律「秒」，比照既有慣例）。先給預設、進 `BaseAppSettings`；實際值待壓測（§10）。

| 設定鍵 | 預設 | 單位 | 出處 | 意義 |
|---|---|---|---|---|
| `monitoring_enabled` | `True` | bool | §2.3/§2.5 | 總開關；`False` → 不掛 log handler、不起 sampler（測試/精簡部署） |
| `monitoring_log_stream_maxlen` | `10000` | 則 | §2.9 | `monitor:stream:logs` 近似上限（`MAXLEN ~`） |
| `monitoring_log_queue_maxsize` | `1000` | 則 | §2.3 | log handler 記憶體佇列上限；滿即丟最舊 |
| `monitoring_log_flush_interval_seconds` | `1` | 秒 | §2.3 | 背景 flush 週期 |
| `monitoring_log_flush_batch_size` | `100` | 則 | §2.3 | 單次 flush 批次上限 |
| `monitoring_log_push_enabled` | `False` | bool | §2.3 | 是否對 `monitor.logs` 即時 tail 推播（高頻，預設關） |
| `monitoring_db_sample_interval_seconds` | `5` | 秒 | §2.5 | DB 狀態採樣週期（對齊股價折線圖 5 秒更新節奏） |
| `monitoring_db_stream_maxlen` | `10000` | 則 | §2.9 | `monitor:stream:db` 近似上限（Stream 用） |
| `monitoring_db_retention_hours` | `24` | 小時 | §2.5/§2.7 | Sorted Set 時間保留窗；`ZREMRANGEBYSCORE` 每 tick 維護 |
| `monitoring_db_sorted_set_key` | `"monitoring:db:history"` | str | §2.5/§2.7 | Sorted Set Redis key（股價式折線圖歷史查詢） |
| `monitoring_sampler_leader_lease_seconds` | `30` | 秒 | §2.5 | leader lease；須 `≥ 2×` 採樣週期 |
| `monitoring_query_max_limit` | `500` | 則 | §2.7 | 查詢單頁上限（防重負載） |
| `monitoring_retention_seconds` | `604800`（7d） | 秒 | §2.9 | 可選按時間修剪 Stream（`MINID`）；`0` = 只靠 `MAXLEN` |

> **關閉/降級語意**：`monitoring_enabled=False` 時，log handler 與 sampler 不啟動，查詢端點仍可讀既有 stream（若有）；測試預設關（`APP_ENV=test`）避免背景 task 與真 Redis 依賴。
>
> ⚠️ **跨規格依賴**：`GET /monitoring/db/history` 的「僅帶 end_ms / 無參數」情境使用 `monitoring_infra_default_query_hours`（定義於 `infra-monitoring.md §4.3`，預設 `1` 小時）作為預設查詢窗口，未在本表另行定義。此為刻意共享（兩個 Sorted Set API 語意完全對稱），修改此值將同時影響 `/db/history` 與 `/infra`。

---

## 5. 流程圖

```
日誌擷取（非阻塞）：
  app logging ─ RedisStreamLogHandler.emit() ─ put_nowait(有界佇列) ─(滿→丟最舊)
    背景 flush task：每 1s / 滿 100 則 → append_many（pipeline XADD × N，1 round-trip）MAXLEN ~ 10000
                                       └(可選) Publisher.to_topic("monitor.logs", event)

DB 狀態採樣（多實例單 leader）：
  每 5s：SET monitor:sampler:leader NX EX 30 ─ 搶到? ─否→ 待命
    └是→ probe.sample()（短命 session 讀 SHOW GLOBAL STATUS / PROCESSLIST + pool）
          ├ Store.append("monitor:stream:db", sample) MAXLEN ~          ← Stream（游標分頁 / WS tail）
          ├ Publisher.to_topic("monitor.db", event(sample)) → 各實例 bridge → 訂閱者 {event}
          └ redis.zadd("monitoring:db:history", {json(sample): ts_ms})  ← Sorted Set（股價式折線圖）
            redis.zremrangebyscore("monitoring:db:history", 0, ts_ms - 24h)

前端標準用法（股價式折線圖）：
  開頁：GET /monitoring/db/history?start_ms=now-1h（拉近 1 小時歷史 snapshots）
      → WS subscribe "monitor.db"（每 5 秒接收增量 event，append 到圖表）

查詢（HTTP，游標分頁）：
  GET /monitoring/logs?level=ERROR&since=&cursor=&limit=50
    → get_current_admin (+require_min_admin_role) → LogQueryService.query
    → Store.query(XRANGE 區間, app 端篩 level) → { items, next_cursor }

前端看板標準用法（補齊 WS at-most-once gap）：
  開頁：GET .../db?limit=N（拉最近歷史）→ 再 WS subscribe "monitor.db"（接續即時）
```

---

## 6. 安全性考量

- **僅 admin**：所有端點走 `get_current_admin`（`role=1`+active）；敏感者再 `require_min_admin_role`（logs 建議 `SUPER_ADMIN`）。WS 即時走 `TOPIC_MIN_ROLE` 授權（§2.6）。
- **日誌遮罩**：入 Store 前已過 `_EmailMaskFilter`（email）＋既有 service 層遮罩；close/error/推播/查詢皆不含明文敏感（沿用既有 masking 慣例）。**新增機密欄位務必先擴充遮罩再落地**。
- **DB 狀態最小揭露**：`information_schema.PROCESSLIST`（`INFO` 欄）可能含他人 query 文字/使用者 → 首期**只聚合計數與時長、不回原始 query 文字**；若未來要回 query 文字，限 `SUPER_ADMIN` 且遮罩參數。監控帳號僅授 `PROCESS`／`REPLICATION CLIENT` 唯讀權限（§2.4）。
- **資源上限（防 DoS）**：stream `MAXLEN`/`retention` 防記憶體膨脹；查詢 `limit`/掃描窗上限防重負載；log 佇列有界防灌爆。
- **非關鍵路徑隔離**：監控元件例外絕不冒泡業務請求（§2.10）；監控失效不得影響登入/CRUD 等核心流程。
- **不外洩**：查詢/推播錯誤訊息不夾帶敏感；Store key 命名不含機密。

---

## 7. TDD 測試計畫（先寫、先看到 RED）

### 7.0 測試地基
- `fakeredis` 的 **Stream 支援（`XADD`/`XRANGE`/`XLEN`/`XTRIM`/`MAXLEN ~`）需先以拋棄式 spike 驗證**（比照 websocket §7.0-a 對 pub/sub 的驗證）；若某 op 不支援，該測試改注入假 Store。
- DB probe 測試：SQLite 無 `SHOW STATUS`/`information_schema.PROCESSLIST` → 注入**假 `DbStatsProbe`**（回固定 `DbSample`）測 Service/端點；`MariaDbStatsProbe` 的 SQL 以 spike/真 MariaDB 煙霧測試涵蓋（§8 step 6）。
- 時間相關（sampler 週期、leader lease）**不 sleep**：以極小 interval 覆寫 config、或注入假時鐘/手動觸發一輪。

### 7.1 Store（unit, fakeredis）
- `RedisStreamStore.append` → `XADD` 寫入、回 entry id；超過 `maxlen` → 最舊被修剪（`XLEN` 有界）。
- `RedisStreamStore.append_many` → 批次寫入 N 筆、回傳長度為 N 的 id list；寫入效果與 N 次 `append` 相同；超過 `maxlen` 同樣觸發修剪。
- `query` 依 `since/until` 對應 ID 區間取回；`cursor` 續頁不重不漏；空區間回空 + `next_cursor=None`。

### 7.2 日誌擷取（unit）✅
- `RedisStreamLogHandler.emit` → 佇列增一；**佇列滿 → 丟最舊、不拋例外、不阻塞**（best-effort）。
- `run_log_flusher` 一輪（batch 有 N 筆）→ 只呼叫**一次** `store.append_many`（pipeline N→1 round-trip），驗 spy 計數 == 1（非 N 次 `append`）；遮罩 filter 生效。
- flush 遇 Store 例外 → 整批 log warning、循環不中斷（all-or-nothing 符合 best-effort 語意）；patch `store.append_many` 驗不拋。

### 7.3 日誌查詢（integration）
- `GET /monitoring/logs`：非 admin → 401/403；admin → 200 + `items`/`next_cursor`。
- `level`/`request_id` 篩選命中；`since/until` 區間正確；`limit` 上限被 clamp。

### 7.4 DB 狀態（unit + integration）
- `PoolStatsProbe` 回 pool 指標；退化 probe 在非 MariaDB 標 `unsupported`。
- `GET /monitoring/db`（注入假 probe）→ 200 + 快照結構；非 admin 拒絕。

### 7.5 採樣器（unit, fakeredis）
- 一輪採樣 → `Store.append` 有一筆 + `Publisher.to_topic("monitor.db")` 被呼叫（同一份樣本）。
- **多實例 leader**：兩個 sampler 共用一 fakeredis → 同一輪只有一個採樣（另一個未搶到 lease）。
- **Sorted Set 雙寫**：
  - 帶 `sorted_set_key` → `_tick()` 後 `ZCARD == 1`，score 等於 `ts`（`test_tick_writes_to_sorted_set`）。
  - 舊於 `retention_hours` 的條目被 `ZREMRANGEBYSCORE` 清除（使用 `NowProbe` 注入當前 epoch ms，確保 cutoff 為正值）（`test_tick_sorted_set_clears_old_entries`）。
  - 未傳 `sorted_set_key` → 不寫 Sorted Set，`ZCARD == 0`（`test_tick_no_sorted_set_when_key_not_set`）。
- 採樣中 probe 拋例外 → 只 log、循環不中斷（best-effort）。

### 7.6 即時推播 + topic 授權（integration，重用 ws_client）
- 訂閱 `monitor.db` → sampler/手動 `to_topic` → 收到 `event`。
- `monitor.logs` 越權（低於門檻的 admin 訂閱）→ `error`、連線續存（沿用 websocket §2.9）。

### 7.7 歷史查詢（integration）
- `GET /monitoring/metrics/db?since=&until=` → range 回放、游標分頁；未知 `name` → **200 + 空 items**（對應 Redis Stream 上無資料；Stream 查詢語意為「空區間回空頁」，不視為請求錯誤，由呼叫端依業務決定是否提示）。

### 7.8 DB 歷史 Sorted Set API（integration，`test_monitoring_db_history_api.py`）
- 無 token → 401；空 Sorted Set → 200 + `snapshots=[]`。
- 有 3 筆（亂序 ZADD）→ 200 + `snapshots` 以 ts 升冪（oldest-first）。
- `start_ms` + `end_ms` → 只回範圍內筆數。
- 只帶 `start_ms` / 只帶 `end_ms` / 無參數 → 各情境正確篩選。
- `start_ms >= end_ms` → 400；範圍超出 `monitoring_db_retention_hours` → 400。

### 7.9 非關鍵路徑隔離
- Store/Redis 不可用時，log handler `emit` 仍不拋、業務請求不受影響（監控失效隔離）。

---

## 8. 實作順序（TDD 里程碑）

0. **前置確認**：WS 骨架（`Publisher`/`topics`/lifespan task）已就緒（websocket 已實作）；spike 驗 `fakeredis` streams（§7.0）。
1. `dtos/monitoring.py`（LogEntry/DbSample/Page）+ `monitoring_*` config 進 `BaseAppSettings`（§4.1）。
2. `TimeSeriesStore` 介面 + `RedisStreamStore`（`append`/`append_many`/`query`）（7.1）；`append_many` 以 `pipeline(transaction=False)` 實作。
3. `RedisStreamLogHandler` + `run_log_flusher` + `setup_logging` 掛載（非測試、`monitoring_enabled`）（7.2）。
4. `LogQueryService` + `GET /monitoring/logs`（授權 + 游標分頁）（7.3）。
5. `DbStatsProbe`（MariaDb/Pool/退化）+ `DbStatsService` + `GET /monitoring/db`（7.4）。
6. `MonitoringSampler`（leader lease + 採樣 → 落地 + 推播）+ lifespan 啟停（7.5/7.6）。
7. `MetricQueryService` + `GET /monitoring/metrics/{name}`（歷史 range）（7.7）。
8. topic 授權（`TOPIC_MIN_ROLE["monitor.db"/"monitor.logs"]`）+ 資源上限 + 非關鍵路徑隔離（7.6/7.9、§6）。
9. **MonitoringSampler Sorted Set 雙寫**（`sorted_set_key`/`retention_hours`）+ `GET /monitoring/db/history`（`DbHistoryResponse`，ZRANGEBYSCORE）（7.5 Sorted Set 測試 + 7.8）。
10. 提交前檢查全綠（ruff / ruff format / pyright / pytest）；真 Redis + 真 MariaDB（`SHOW GLOBAL STATUS`／`PROCESSLIST`）煙霧測試。

---

## 9. 已定案決策

- ✅ **統一時序骨架**：三項能力（logs/db/歷史）同構——`採集 → TimeSeriesStore → 即時 WS 推播 → HTTP 查詢`；即時 best-effort、歷史由 Store 保證，前端「先拉歷史再訂閱即時」補齊 gap。
- ✅ **Redis Stream 為 hot store（已定案）**，藏在 `TimeSeriesStore` 介面後；有界（`MAXLEN ~`/可選 `MINID`）、非長期歸檔；游標＝entry ID。**未來接外部聚合器/TSDB＝換 adapter、上層零改**。
- ✅ **日誌擷取非阻塞**：`logging.Handler` → 有界佇列（滿即丟）→ 背景 flush 呼叫 `store.append_many()`（Redis pipeline `transaction=False`，N 筆 → 1 round-trip，避免串行 N 次 XADD 的吞吐量懸崖）；錯誤語意為 all-or-nothing，與 best-effort 監控一致；沿用既有遮罩 filter；`APP_ENV=test`/`monitoring_enabled=False` 不啟用。
- ✅ **DB 狀態遵守 ORM 可攜性邊界**：可攜基準 `PoolStatsProbe`（讀 `engine.pool`、無 raw SQL、任何後端零改）＋ 引擎專屬擴充（`MariaDbStatsProbe` 讀 `SHOW GLOBAL STATUS`／`PROCESSLIST`／`TABLES`）藏在 `DbStatsProbe` 介面後、依 `db_dialect` 自動選用；換 DB＝新增一支 probe，Service/API/DTO/前端零改，無對應 probe 自動退化只回 pool。走短命 session（`get_session_factory`）；監控帳號僅授 `PROCESS`／`REPLICATION CLIENT` 唯讀權限。
- ✅ **Sampler lifespan 啟停 + Redis leader lease**：多實例只單一採樣者，避免 N 倍寫入/重複推播；`lease ≥ 2× interval`。
- ✅ **MonitoringSampler 雙寫（Stream ＋ Sorted Set）**：Stream（`XADD MAXLEN ~`）供 WS tail / 游標分頁；Sorted Set（`ZADD score=epoch_ms` + `ZREMRANGEBYSCORE` 時間保留）供 `GET /monitoring/db/history` 股價式折線圖查詢（`ZRANGEBYSCORE start_ms end_ms`）。兩者職責不同、共用同一份採樣，採樣間隔從 15 秒縮短至 **5 秒**，Sorted Set 保留 24 小時。
- ✅ **`GET /monitoring/db/history`**：4 種時間參數情境（帶兩者/只 start/只 end/無參數）與 `/infra` 語意一致；`start >= end` → 400；範圍超出 `monitoring_db_retention_hours` → 400；Redis 不可用 → 503；回 `DbHistoryResponse(snapshots=list[DbSample])`（oldest-first）。
- ✅ **即時重用 WS `Publisher.to_topic` 與 `event` 封套**，`monitor.*` topic 走既有 `TOPIC_MIN_ROLE` 授權（logs 建議 SUPER_ADMIN、db 建議 VIEWER）。
- ✅ **查詢游標分頁 + app 端篩選**，`limit` 上限；DB 狀態不回原始 query 文字（最小揭露）。
- ✅ **監控為非關鍵路徑**：全程 best-effort，例外絕不冒泡業務請求。
- ✅ 分層：API（授權/委派）→ Service → Store/Probe → Redis/DB；採集元件於 lifespan 啟停（比照 `WsBridge`）。
- ✅ **測試**：`fakeredis` streams（先 spike 驗）、假 `DbStatsProbe`、假時鐘/手動觸發；unit + integration 全綠。

## 10. 待確認事項（Open Questions）

1. **DB 指標範圍**：是否納入 slow query（`performance_schema.events_statements_summary_by_digest`，需啟用 P_S）、replication lag（`SHOW REPLICA STATUS`）、InnoDB row locks/deadlocks（`information_schema.INNODB_METRICS`／`performance_schema.data_lock_waits`）？首期只 pool + 連線聚合 + DB size。
2. **多後端**：DB 狀態查詢為 MariaDB/MySQL 專屬；正式只跑 MariaDB？（測試 SQLite 走退化 probe）。若未來支援其他 DB，需對應 probe。
3. **長期歸檔**：Store 為有界 hot buffer；長保留/趨勢分析要不要 Stream → S3/外部 TSDB 的歸檔管線？（介面已預留 adapter）。
4. **外部聚合器切換時機**：何時把 `LogStore` 換成 CloudWatch/ELK adapter（取決於 log 量與維運）；切換後 app 內 stream 可退為純即時 tail 或移除。
5. **即時 log tail 預設**：`monitor.logs` 高頻，預設關（`monitoring_log_push_enabled=False`）；是否要 server 端過濾（只推 `WARNING+`）以降量？
6. **告警（alerting）**：閾值告警/通知（如 pool 耗盡、error 突增）本期非目標；建議承本骨架另立 `alerting.md`（消費 stream/推播）。
7. **參數壓測**：`*_maxlen`／`sample_interval`／`flush_*`／`retention` 的實際值待壓測與磁碟/記憶體預算定案（鍵名與預設已定）。
8. **業務指標擴充**：本骨架 `TimeSeriesStore` 與 `monitor.*` 命名空間可承載未來任意業務時序指標；命名規範（`monitor.<domain>.<metric>`）與各自最低 `admin_role` 待各業務規格定義。
9. **cid/游標穩定性**：修剪（`MAXLEN`/`MINID`）後舊游標超出保留窗 → 回空頁（不報錯）；此語意是否需前端特別處理（提示「歷史已過期」）待前端契約定案。
```
