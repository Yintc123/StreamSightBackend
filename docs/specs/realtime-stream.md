# 規格書：即時串流資料生成器（Realtime Stream）

> 狀態：**草案（尚未實作）** ／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> **語言**：繁體中文。
>
> 🔗 依賴既有機制：WS 骨架（[`websocket.md`](./websocket.md)：`Publisher.to_topic`／`TOPIC_MIN_ROLE` 授權掛勾／lifespan 背景 task 慣例）、Redis（`app/core/redis`，測試以 `fakeredis`）、`AdminRole`（IntEnum，[`enum-int.md`](./enum-int.md)）。
>
> ⚠️ **範圍**：本規格定義後端**即時串流資料生成器**——每秒產生一筆模擬數值、透過既有 WS topic 機制推送給訂閱的前端連線。**不含前端 WS client**（見 Streamlit [`04-realtime-monitor.md`]（接後端 WS Cycle 段落））。

---

## 0. 功能總覽（先讀這裡）

**一句話**：在 FastAPI lifespan 掛一個每秒跑的 asyncio 背景 task，生成決定性模擬值後以 `Publisher.to_topic("realtime.stream", ...)` 推播給所有訂閱該 topic 的已認證 admin WS 連線。

**三個定案前提（見 §2）**：

| 維度 | 定案 | 影響 |
|---|---|---|
| 推播管道 | **重用既有 WS `Publisher.to_topic`** | 不新建 channel；topic 加一行 `TOPIC_MIN_ROLE` 即通 |
| 生成演算法 | **決定性 SHA-256（同前端 `sample_value`）** | 同 tick 前後端值可驗算；測試無需 mock 亂數 |
| 多實例 | **不需 leader lease** | 每實例獨立 tick 遞增、前端按到達序顯示，多實例推重複可接受（前端 trim 最多 60 筆） |

**改動清單（三處，合計約 40 行）**：

| 檔案 | 動作 | 行數 |
|---|---|---|
| `app/services/ws/topics.py` | 加一行 `"realtime.stream": AdminRole.VIEWER` | 1 |
| `app/services/realtime/streamer.py` | **新建**；生成器純函式 + 背景 task | ~25 |
| `app/app.py` | lifespan 掛 task、shutdown 取消 | ~8 |

---

## 1. 背景與目標

Streamlit 即時監控頁（`04-realtime-monitor.md`）在 mock 階段以前端決定性生成器模擬每秒資料；接後端後須由 FastAPI 真正推送——符合 [ADR 0001](../../StreamSightStreamlit/docs/decisions/0001-realtime-architecture.md)「WebSocket 為硬性技術需求」的決策。既有 WS 骨架（ticket 認證、topic 訂閱、Redis pub/sub 跨實例、背壓管理）已完整就緒；本規格只需在其上掛新 topic 並啟動生成器 task。

### 目標

- 定義 **`realtime.stream` topic**（授權等級、payload 格式）。
- 定義 **`RealtimeStreamer`** 背景 task（每秒採樣 → 發佈）。
- 確保生成演算法**決定性**（同 tick → 同值），便於跨前後端測試驗算。
- 嚴格 TDD：每個行為先寫失敗測試。

### 非目標

- 前端 WS client 實作（屬 Streamlit 規格）。
- 歷史資料查詢端點（可後續參照 `monitoring.md` 骨架擴充）。
- 閾值判斷（屬前端業務；後端只推原始值）。

---

## 2. 設計決策

### 2.1 Topic 命名與授權

`"realtime.stream"` 對應 `AdminRole.VIEWER`（所有已認證 admin 皆可訂閱），對齊 Streamlit 頁面「存取權限：已登入使用者」規格。

`monitor.logs`（`SUPER_ADMIN`）與 `monitor.db`（`VIEWER`）為現有 topic；本 topic 並列加入，**不改動現有 topic**。

### 2.2 生成演算法：決定性 SHA-256

```
digest = SHA-256(f"{seed}:{tick}")
fraction = int(digest[:8], 16) / 0xFFFFFFFF
value = round(fraction × 100, 1)   # [0.0, 100.0]，一位小數
```

與前端 `lib/realtime.py::sample_value(tick, seed=0)` **完全相同演算法**。選此設計的理由：

1. **可測試**：測試可用任意 tick 驗算期望值，不需 mock 亂數。
2. **前後端可驗算**：切換 `DATA_SOURCE=api` 後，若 `tick` 已知，前端可與後端比對值是否一致——方便 QA 驗收。
3. **值域保證**：SHA-256 取前 32 bit 均勻分布，值落在 `[0.0, 100.0]`，不需 clamp。

> 正式上線若需更擬真的時序訊號（drift、spike），可將演算法換為 `random.uniform` 或加高斯雜訊，而不影響任何介面。

### 2.3 多實例：不需 leader lease

`MonitoringSampler` 因「寫入 Redis Stream + Sorted Set 重複資料代價高」需 leader lease。本 task 只發佈 WS 訊息，重複推送只讓前端偶爾一秒收到兩筆（60 筆 trim 吸收），可接受。**不實作 leader lease**（降低複雜度）。

### 2.4 tick 持久化：Redis INCR

```python
tick = await redis_client.incr("realtime:tick")
```

用 Redis 原子 INCR：
- 跨重啟 tick 連續（值不重來）。
- 多實例各自 INCR，確保各實例 tick 不重疊（值域唯一），前端 trim 自然去重。
- 若 Redis 暫時不可達，`INCR` 失敗 → 只跳過該秒，不斷整個 task。

---

## 3. 資料流

```
lifespan startup
    │
    └── asyncio.create_task(RealtimeStreamer(publisher, redis).run())
                │
                └── 每秒 loop：
                      tick = INCR "realtime:tick"
                      value = _sample(tick)
                      publisher.to_topic("realtime.stream", {type, topic, value, ts})
                              │
                              ▼  Redis PUBLISH "ws:topic:realtime.stream"
                              │
                      WsBridge._dispatch
                              │
                              ▼  manager.send_local(topic="realtime.stream", message=...)
                              │
                      per-conn asyncio.Queue → writer task → WebSocket frame
                              │
                              ▼
                      Streamlit background thread（RealtimeWsClient._on_reading）
```

---

## 4. WS 訊息格式

### 4.1 `server → client`：data 訊息

```json
{
  "type":  "data",
  "topic": "realtime.stream",
  "value": 42.3,
  "ts":    "2026-07-19T12:00:01.000000+00:00"
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `type` | `"data"` | 固定字串；前端依此識別為業務資料 |
| `topic` | `"realtime.stream"` | 前端依此路由（同一連線可訂多 topic） |
| `value` | `float` | `[0.0, 100.0]`，一位小數 |
| `ts` | ISO 8601 UTC | `datetime.now(UTC).isoformat()`；前端轉本地時區後顯示 |

### 4.2 客戶端訂閱（已由 websocket.md 定義）

1. `POST /ws/ticket`（Bearer JWT）→ 取 `ticket`
2. `WS /ws?ticket={ticket}` → accept + welcome
3. client 送 `{"type": "subscribe", "topic": "realtime.stream"}`
4. server 回 `{"type": "subscribed", "topic": "realtime.stream"}`
5. 之後每秒收到 §4.1 data 訊息

---

## 5. 改動清單

### 5.1 `app/services/ws/topics.py`

```python
TOPIC_MIN_ROLE: dict[str, AdminRole] = {
    "monitor.logs":    AdminRole.SUPER_ADMIN,
    "monitor.db":      AdminRole.VIEWER,
    "realtime.stream": AdminRole.VIEWER,   # ← 新增
}
```

### 5.2 `app/services/realtime/streamer.py`（新建）

```python
"""即時串流資料生成器 — 每秒採樣並發佈至 realtime.stream topic。"""

import asyncio
import hashlib
import logging
from datetime import UTC, datetime

import redis.asyncio as redis

from app.services.ws.publisher import Publisher

logger = logging.getLogger(__name__)

STREAM_TOPIC = "realtime.stream"
_TICK_KEY = "realtime:tick"


def sample_value(tick: int, seed: int = 0) -> float:
    """決定性取值（同前端 lib/realtime.py::sample_value，§2.2）。"""
    digest = hashlib.sha256(f"{seed}:{tick}".encode()).hexdigest()
    return round(int(digest[:8], 16) / 0xFFFFFFFF * 100, 1)


class RealtimeStreamer:
    """每秒生成模擬值並發佈到 realtime.stream topic（§2.3/§2.4）。"""

    def __init__(self, publisher: Publisher, redis_client: redis.Redis) -> None:
        self._publisher = publisher
        self._redis = redis_client

    async def run(self) -> None:
        """背景迴圈（於 lifespan 以 asyncio.create_task 啟動）。"""
        while True:
            try:
                await asyncio.sleep(1.0)
                tick = int(await self._redis.incr(_TICK_KEY))
                await self._publisher.to_topic(STREAM_TOPIC, {
                    "type":  "data",
                    "topic": STREAM_TOPIC,
                    "value": sample_value(tick),
                    "ts":    datetime.now(UTC).isoformat(),
                })
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("realtime_streamer: error, skip tick")
```

### 5.3 `app/app.py` lifespan

```python
# startup（monitoring 區塊之後）
from app.services.realtime.streamer import RealtimeStreamer
streamer_task = asyncio.create_task(
    RealtimeStreamer(Publisher(redis_client), redis_client).run(),
    name="realtime-streamer",
)

# shutdown（infra_sampler 之前）
if not streamer_task.done():
    streamer_task.cancel()
    with suppress(asyncio.CancelledError):
        await streamer_task
```

---

## 6. TDD 測試計畫

依 `CLAUDE.md` Red→Green→Refactor，每項先寫失敗測試：

### 6.1 Unit — `sample_value`（`tests/unit/realtime/test_streamer.py`）

1. 決定性：`sample_value(5) == sample_value(5)`。
2. 值域：掃 100 個 tick，皆落在 `[0.0, 100.0]`，`round(v, 1) == v`。
3. 對齊前端：`sample_value(tick)` 產生與前端 `lib/realtime.sample_value(tick)` 相同值（若可跨測試驗算）。

### 6.2 Unit — `RealtimeStreamer.run`（mock Publisher + fakeredis）

4. **發佈一次**：讓 task 跑一次 sleep cycle，驗 `publisher.to_topic` 被呼叫一次，payload 含 `type="data"`、`topic="realtime.stream"`、`value`（float）、`ts`（ISO8601 含 UTC offset）。
5. **tick 遞增**：連跑兩次，驗 Redis `realtime:tick` 值遞增（第一次 `incr` 後為 1、第二次為 2）。
6. **Publisher 例外不停 task**：mock `publisher.to_topic` 拋 `Exception`，task 仍在跑（不 cancel）；下一次週期照常執行。
7. **CancelledError 正常退出**：取消 task → `CancelledError` 傳播而非被吞（`run()` 重新 raise）。

### 6.3 Integration — WS 訊息投遞

8. 用 `httpx_ws`（`ASGITransport`）建立真實 WS 連線、訂閱 `realtime.stream`；讓 `RealtimeStreamer` task 執行一次週期，驗前端收到 `{"type":"data","topic":"realtime.stream","value":...,"ts":...}`。

---

## 7. 待確認事項

1. **`APP_ENV=test` 是否啟動 streamer**：現有 `monitoring_enabled` 總開關可作為模型（`realtime_stream_enabled` config，測試預設關）。待確認測試策略（integration test 獨立控制）。
2. **多實例 tick 競爭**：各實例 `INCR` 各拿到不同 tick，前端 60 筆 buffer 可能一秒收到多筆。目前決策接受（§2.3）；若日後要求每秒恰一筆，再補 leader lease。
3. **值生成替換**：決定性 SHA-256 → `random.uniform`（更擬真）屬純演算法替換，`sample_value` 簽章不變，可後續以 config flag 切換。
