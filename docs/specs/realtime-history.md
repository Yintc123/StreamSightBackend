# 規格書：即時串流歷史資料儲存與查詢（Realtime History）

> 狀態：**草案（尚未實作）** ／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> **語言**：繁體中文。
>
> 🔗 依賴：[`realtime-stream.md`](./realtime-stream.md)（`RealtimeStreamer` task 與 `sample_value`）、MariaDB（`app/core/db`，SQLAlchemy 2.x async）、Alembic migration、`AdminRole`（IntEnum）。
>
> ⚠️ **範圍**：本規格定義即時串流資料的**落地儲存（批次 INSERT）與歷史查詢 API**。WS 推送邏輯不在此處修改——WS 仍每秒推、DB 每 60 筆批次落地，兩者獨立。

---

## 0. 功能總覽（先讀這裡）

**一句話**：`RealtimeStreamer` 在記憶體累積每秒生成的值，滿 60 筆時做一次 bulk INSERT 寫入 `realtime_readings` table；前端可透過 `GET /realtime/history` 以時間範圍查詢歷史資料。

**四個定案前提（見 §2）**：

| 維度 | 定案 | 影響 |
|---|---|---|
| 批次大小 | **60 筆（約 1 分鐘）** | 歷史資料最差落後 ~59 秒；DB 寫入降至 1,440 次/天 |
| 批次 buffer 位置 | **Streamer 實例記憶體（Python list）** | 零額外基礎設施；進程崩潰最多丟 60 筆模擬資料（可接受） |
| 授權 | **`AdminRole.VIEWER`**（同 WS topic） | 所有已認證 admin 皆可查詢歷史 |
| WS 推送 | **不受影響** | 每秒仍推；batch 邏輯獨立在 Streamer 內 |

**改動清單（五處，合計約 80 行）**：

| 檔案 | 動作 |
|---|---|
| `app/models/realtime_reading.py` | **新建**；`RealtimeReading` ORM model |
| `app/repositories/realtime_reading.py` | **新建**；`bulk_insert` + `list_history` |
| `app/services/realtime/streamer.py` | 修改；加 `_batch` list + `_flush` 邏輯 |
| `app/api/routers/realtime/` | **新建**；`GET /realtime/history` |
| `alembic/versions/` | **新建**；`realtime_readings` table + `ts` index |

---

## 1. 背景與目標

`RealtimeStreamer`（[`realtime-stream.md`](./realtime-stream.md)）每秒生成一筆模擬值並推送 WS，但目前資料不落地——WS 斷線即消失，無法查詢歷史趨勢。本規格補上落地與查詢能力。

### 目標

- 每 60 筆批次寫入 MariaDB，保存完整時間序列。
- 提供 `GET /realtime/history` 以時間範圍查詢歷史紀錄。
- 批次機制對 WS 推送零干擾。
- 嚴格 TDD：每個行為先寫失敗測試。

### 非目標

- Retention 自動清理（延後；見 §7）。
- 多 stream 支援（目前固定 `realtime.stream` 單一 topic）。
- 即時（< 1 秒）歷史查詢精度。

---

## 2. 設計決策

### 2.1 批次大小：60 筆

每秒一筆 → 60 筆 ≈ 1 分鐘一次 flush：

- DB 寫入：86,400 次/天（每秒） → **1,440 次/天（每分鐘）**，降 60 倍。
- 查詢落後上限：59 秒（最差情況：查詢時當前 batch 剛累積 1 筆，前一批 flush 於 59 秒前）。
- 對「歷史趨勢查詢」場景 59 秒落後**完全可接受**。

### 2.2 Buffer 位置：Streamer 實例記憶體

```python
self._batch: list[dict] = []
```

- 零額外基礎設施（不用 Redis List、不用額外 table）。
- 進程崩潰最多丟 60 筆模擬資料——資料為模擬值，業務影響為零。
- 若日後改為真實感測器資料，再評估 Redis List 做持久 buffer。

### 2.3 Flush 觸發：count-based（`len >= 60`）

不用 time-based（`asyncio.sleep(60)`）的原因：

- Streamer 已有 `asyncio.sleep(1.0)` 計時，count 自然等於秒數；不需另起 timer。
- 應用關閉時（`CancelledError`）需在 `finally` flush 剩餘資料（見 §5.2）。

### 2.4 授權：`AdminRole.VIEWER`

對齊 WS topic 的最低授權等級；歷史資料敏感度不高於即時資料。

---

## 3. 資料流

### 3.1 寫入流

```
RealtimeStreamer.run()
  └── 每秒：
        tick = INCR "realtime:tick"
        value = sample_value(tick)
        await publisher.to_topic(...)         # WS 推送（不變）
        self._batch.append({value, ts})
        if len(self._batch) >= 60:
            await self._flush()               # bulk INSERT → MariaDB
            self._batch.clear()

  └── CancelledError（應用關閉）：
        finally: await self._flush()          # flush 剩餘資料
```

### 3.2 查詢流

```
GET /realtime/history?from=&to=&size=
  └── require_min_admin_role(VIEWER)
  └── RealtimeHistoryService.list_history(from, to, size)
        └── RealtimeReadingRepository.list(from_dt, to_dt, size)
              └── SELECT * FROM realtime_readings
                  WHERE ts >= from_dt AND ts < to_dt
                  ORDER BY ts ASC
                  LIMIT size
```

---

## 4. Table Schema

### 4.1 `realtime_readings`

```sql
CREATE TABLE realtime_readings (
    id    BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    value FLOAT           NOT NULL,
    ts    DATETIME(6)     NOT NULL        -- UTC，微秒精度
);

CREATE INDEX ix_realtime_readings_ts ON realtime_readings (ts);
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | `BIGINT UNSIGNED` | 自增 PK；不對外暴露（回應以 `ts` 為主鍵意義） |
| `value` | `FLOAT` | `[0.0, 100.0]`，一位小數 |
| `ts` | `DATETIME(6)` UTC | 生成時刻（UTC）；查詢以此欄做範圍篩選 |

> 不設 `created_at`（`ts` 即生成時刻，不需另一個時間欄）。

### 4.2 SQLAlchemy Model

```python
# app/models/realtime_reading.py
class RealtimeReading(Base):
    __tablename__ = "realtime_readings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False,
                                          index=True)  # UTC 存入，無 tz info
```

---

## 5. 改動清單

### 5.1 `app/repositories/realtime_reading.py`（新建）

```python
class RealtimeReadingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert(self, rows: list[dict]) -> None:
        """rows: [{"value": float, "ts": datetime(UTC)}]"""
        await self._session.execute(insert(RealtimeReading), rows)
        await self._session.commit()

    async def list(
        self,
        from_dt: datetime,
        to_dt: datetime,
        size: int = 1000,
    ) -> list[RealtimeReading]:
        result = await self._session.execute(
            select(RealtimeReading)
            .where(RealtimeReading.ts >= from_dt)
            .where(RealtimeReading.ts < to_dt)
            .order_by(RealtimeReading.ts.asc())
            .limit(size)
        )
        return list(result.scalars().all())
```

### 5.2 `app/services/realtime/streamer.py`（修改）

```python
BATCH_SIZE = 60

class RealtimeStreamer:
    def __init__(self, publisher: Publisher, redis_client, repo: RealtimeReadingRepository) -> None:
        self._publisher = publisher
        self._redis = redis_client
        self._repo = repo
        self._batch: list[dict] = []

    async def _flush(self) -> None:
        if self._batch:
            await self._repo.bulk_insert(list(self._batch))
            self._batch.clear()

    async def run(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                tick = int(await self._redis.incr(_TICK_KEY))
                ts = datetime.now(UTC).replace(tzinfo=None)   # 存 naive UTC
                value = sample_value(tick)
                await self._publisher.to_topic(STREAM_TOPIC, {
                    "type": "data", "topic": STREAM_TOPIC,
                    "value": value,
                    "ts": datetime.now(UTC).isoformat(),       # 帶 +00:00 給 WS client
                })
                self._batch.append({"value": value, "ts": ts})
                if len(self._batch) >= BATCH_SIZE:
                    await self._flush()
        except asyncio.CancelledError:
            await self._flush()    # 應用關閉前 flush 剩餘資料
            raise
```

### 5.3 `app/api/routers/realtime/router.py`（新建）

```python
@router.get("/realtime/history", response_model=HistoryPage)
async def list_history(
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    size: int = Query(1000, ge=1, le=5000),
    _: Admin = Depends(require_min_admin_role(AdminRole.VIEWER)),
    service: RealtimeHistoryService = Depends(get_realtime_history_service),
) -> HistoryPage:
    items = await service.list_history(from_, to, size)
    return HistoryPage(items=items, from_=from_, to=to)
```

**Response schema**：

```json
{
  "items": [
    {"value": 42.3, "ts": "2026-07-19T12:00:01.000000Z"},
    ...
  ],
  "from": "2026-07-19T11:00:00Z",
  "to":   "2026-07-19T12:00:00Z"
}
```

### 5.4 Alembic Migration（新建）

```python
def upgrade() -> None:
    op.create_table(
        "realtime_readings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_realtime_readings_ts", "realtime_readings", ["ts"])

def downgrade() -> None:
    op.drop_table("realtime_readings")   # 連帶移除 index（避免 MariaDB FK 順序問題）
```

---

## 6. TDD 測試計畫

依 `CLAUDE.md` Red→Green→Refactor，每項先寫失敗測試：

### 6.1 Unit — Repository（`tests/unit/realtime/test_realtime_reading_repo.py`）

1. **bulk_insert 寫入正確筆數**：插入 3 筆 → DB 有 3 筆。
2. **list 範圍查詢**：插入 5 筆不同 ts → `from_dt`/`to_dt` 只回傳區間內的筆數。
3. **list 排序**：結果按 `ts ASC`（最舊在前）。
4. **list size 上限**：插入 100 筆、`size=10` → 只回傳 10 筆。
5. **bulk_insert 空 list**：不拋例外、DB 筆數不變。

### 6.2 Unit — Streamer batch 邏輯（`tests/unit/realtime/test_streamer.py`，新增）

6. **未滿 60 筆不 flush**：執行 59 次 loop → `repo.bulk_insert` 未被呼叫。
7. **滿 60 筆 flush 一次**：執行 60 次 loop → `repo.bulk_insert` 被呼叫一次，傳入 60 筆。
8. **flush 後 batch 清空**：flush 後繼續執行 1 次 loop → `_batch` 長度為 1。
9. **CancelledError 觸發 flush**：執行 30 次後取消 task → `repo.bulk_insert` 被呼叫一次，傳入 30 筆（剩餘資料不丟失）。
10. **flush 例外不停 task**：`repo.bulk_insert` 拋 `Exception` → task 繼續跑（不因 flush 失敗中斷）。

### 6.3 Integration — API（`tests/integration/test_realtime_history_api.py`）

11. **無 token → 401**。
12. **viewer 可查詢**：VIEWER 角色 → 200。
13. **時間範圍過濾正確**：插入 3 筆不同時間 → `from`/`to` 只回傳符合區間的筆數。
14. **size 上限 5000**：`size=9999` → 422（超出上限）。
15. **回應結構正確**：`items[].value`（float）、`items[].ts`（ISO8601 UTC）、`from`、`to`。

---

## 7. 待確認事項

1. **Retention 策略**：**定案——永久保留**。不設自動清理，table 持續成長（86,400 筆/天，約 4 MB/天）。若日後磁碟壓力過大，再評估歸檔或分區策略；現階段不實作。
2. **多實例 flush 競態**：各實例各自 flush 自己的 `_batch`，`ts` 不重疊（各自獨立採樣）；但同一秒可能有多筆（多實例各推一次）。前端查詢時如需去重，依 `ts` 精度（微秒）自然區分，可接受。
3. **`from` 為 query param 保留字**：FastAPI 以 `alias="from"` 繞過，程式碼用 `from_`；需確認 API 文件正確顯示 `from`。
