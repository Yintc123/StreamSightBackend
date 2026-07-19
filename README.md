# StreamSight Backend

FastAPI 非同步後端，為 StreamSight 儀表板提供認證、資料記錄、即時串流、WebSocket 推播與監控等服務。

---

## 技術棧

| 層面 | 技術 |
|------|------|
| 語言 / 套件管理 | Python 3.13+ / [uv](https://docs.astral.sh/uv/) |
| Web 框架 | FastAPI + Uvicorn |
| ORM / Migration | SQLAlchemy 2.x (async) / Alembic |
| 主資料庫 | MariaDB（asyncmy）|
| 測試資料庫 | SQLite in-memory（aiosqlite）|
| 快取 / Pub-Sub | Redis（hiredis；測試用 fakeredis）|
| 密碼雜湊 | Argon2id（argon2-cffi）|
| 欄位加密 | AES-256-CBC（cryptography）|
| JWT | PyJWT（HS256）|
| 監控 | Prometheus client + Redis Streams |
| Lint / Format | Ruff |
| 型別檢查 | Pyright（standard 模式）|
| 測試 | pytest + pytest-asyncio + httpx |

---

## 架構概覽

```
API 層（routers）
    │  輸入驗證、DI、HTTP/WS 處理
    ▼
Services 層
    │  商業邏輯、不變式守衛
    ▼
Repositories 層
    │  DB 存取（SQLAlchemy async）
    ▼
Models 層
    │  SQLAlchemy ORM、DB 約束
    ▼
DB（MariaDB / SQLite）  ←→  Redis（快取、Pub-Sub、Streams）
```

### 身分模型（Principal 雙表設計）

```
principals (id, role)
    ├── users        (principal_id FK, email[加密], name, user_tier, ...)
    │       └── identities  (OAuth / password 多身分憑證)
    └── admins       (principal_id FK, username, password_hash, admin_role, ...)

refresh_tokens  → principals.id（統一擁有者）
records / record_categories（業務資料）
realtime_readings（即時讀值歷史）
```

`principals.role` 是型別判別子（0=User, 1=Admin），建立後不可變，配合複合 FK + CHECK 強制型別—角色一致性。

---

## 快速開始

### 前置需求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)（安裝：`curl -LsSf https://astral.sh/uv/install.sh | sh`）
- Docker + Docker Compose（本機啟動 MariaDB 與 Redis）

### 安裝

```bash
git clone <repo-url>
cd StreamSightBackend
uv sync --dev
```

### 啟動本機依賴服務

canonical 基礎設施（MariaDB + Redis）由 StreamSight infra 提供：

```bash
docker compose -f ../infra/docker-compose.yml up -d
```

或使用本專案 standalone compose（不可與 infra 同時啟動）：

```bash
docker compose up -d
```

### 環境變數

建立 `.env` 並填入以下欄位：

```env
# 執行環境
APP_ENV=local

# 資料庫
DB_HOST=localhost
DB_PORT=3306
DB_USER=streamsight
DB_PASSWORD=your_password
DB_NAME=streamsight

# 金鑰（各需 ≥ 32 字元）
ENCRYPTION_KEY=your-32-char-encryption-key-here!
JWT_SECRET_KEY=your-32-char-jwt-secret-key-here!
REFRESH_TOKEN_HASH_SECRET=your-32-char-refresh-secret-here

# Bootstrap root admin（首次啟動自動 upsert 至 DB）
INITIAL_ADMIN_USERNAME=root
INITIAL_ADMIN_NAME=Root Admin
INITIAL_ADMIN_PASSWORD=your_root_password

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
```

> **安全警告：** `ENCRYPTION_KEY` 一旦有資料寫入即不可更改，否則既有加密欄位將無法解密。

生成高強度金鑰：

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 資料庫 Migration

```bash
uv run alembic upgrade head
```

### 啟動開發伺服器

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Swagger UI：`http://localhost:8000/docs`
- ReDoc：`http://localhost:8000/redoc`

---

## 環境變數完整說明

| 變數 | 說明 | 預設 |
|------|------|------|
| `APP_ENV` | 執行環境：`local` / `development` / `stage` / `production` / `test` | `local` |
| `DB_DIALECT` | SQLAlchemy dialect+driver | `mysql+asyncmy` |
| `DB_HOST` / `DB_PORT` | DB 位置 | `localhost` / `3306` |
| `DB_USER` / `DB_PASSWORD` | DB 帳密 | `streamsight` / — |
| `DB_NAME` | DB 名稱 | `streamsight` |
| `ENCRYPTION_KEY` | AES-256 欄位加密金鑰（≥32 字元，**不可更換**） | — |
| `JWT_SECRET_KEY` | JWT 簽章密鑰（≥32 字元） | — |
| `JWT_ALGORITHM` | JWT 演算法 | `HS256` |
| `JWT_ACCESS_TOKEN_EXPIRE_SECONDS` | access token 效期（秒，上限 24h） | `1800` |
| `REFRESH_TOKEN_HASH_SECRET` | refresh token HMAC pepper（≥32 字元） | — |
| `REFRESH_TOKEN_EXPIRE_SECONDS` | refresh token 效期（秒，上限 90d） | `1209600` |
| `REFRESH_TOKEN_REUSE_GRACE_SECONDS` | reuse 誤判緩解視窗（秒） | `10` |
| `INITIAL_ADMIN_USERNAME` | Bootstrap root admin 帳號 | — |
| `INITIAL_ADMIN_NAME` | Bootstrap root admin 顯示名稱 | — |
| `INITIAL_ADMIN_PASSWORD` | Bootstrap root admin 密碼 | — |
| `REDIS_HOST` / `REDIS_PORT` | Redis 位置 | `localhost` / `6379` |
| `REDIS_USERNAME` / `REDIS_PASSWORD` | Redis 認證（空 = 不啟用） | — |
| `MONITORING_ENABLED` | 監控總開關 | `true` |
| `REALTIME_STREAM_ENABLED` | 即時串流總開關 | `true` |
| `MONITORING_INFRA_ENABLED` | 基礎設施指標採集開關 | `true` |

> 測試環境（`APP_ENV=test`）自動改用 SQLite in-memory，無需外部 DB 或 Redis。

---

## API 端點

### 認證（`/auth`）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/auth/register` | 註冊新 User，回傳 access + refresh token（201）|
| POST | `/auth/login` | User 登入，回傳 token |
| POST | `/auth/refresh` | Refresh token 輪換（返回新 access + refresh）|
| POST | `/auth/logout` | 撤銷單一 refresh token（204）|
| POST | `/auth/logout-all` | 撤銷當前 principal 所有 refresh token（204）|

### Admin（`/admin`）

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| POST | `/admin/auth/login` | 公開 | Admin 登入 |
| GET  | `/admin/me` | 任一 Admin | 取得當前 Admin 資訊 |
| POST | `/admin/me/password` | 任一 Admin | 修改自己密碼（204）|
| GET  | `/admin/admins` | SUPER_ADMIN | 列出 Admin（status 篩選、分頁）|
| POST | `/admin/admins` | SUPER_ADMIN | 新增 Admin（201）|
| GET  | `/admin/admins/{id}` | SUPER_ADMIN | 取得 Admin 明細 |
| PATCH | `/admin/admins/{id}` | SUPER_ADMIN | 更新 Admin（name）|
| PUT  | `/admin/admins/{id}/role` | SUPER_ADMIN | 變更 Admin 角色 |
| POST | `/admin/admins/{id}/archive` | SUPER_ADMIN | 封存 Admin |
| POST | `/admin/admins/{id}/unarchive` | SUPER_ADMIN | 取消封存 |
| DELETE | `/admin/admins/{id}` | SUPER_ADMIN | 軟刪除 Admin（200 + 更新後狀態）|
| POST | `/admin/admins/{id}/restore` | SUPER_ADMIN | 還原軟刪除 Admin |

### 資料記錄（`/records`）

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET  | `/records/categories` | VIEWER+ | 分類下拉清單 |
| GET  | `/records` | VIEWER+ | 分頁列表（category、keyword、date_from/to、sort、include_deleted）|
| POST | `/records` | EDITOR+ | 新增單筆記錄（201）|
| POST | `/records/bulk` | EDITOR+ | 批次匯入（逐列驗證，錯誤不中斷）|
| GET  | `/records/{id}` | VIEWER+ | 取得單筆記錄 |
| PATCH | `/records/{id}` | EDITOR+ | 更新記錄 |
| DELETE | `/records/{id}` | EDITOR+ | 軟刪除（204）|

### 即時資料（`/realtime`）

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET  | `/realtime/history` | VIEWER+ | 查詢歷史讀值（`from`、`to` 時間範圍，最多 5000 筆）|

### WebSocket（`/ws`）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/ws/ticket` | 已認證 Admin 換取短命單次 ticket（TTL 預設 180s）|
| WS   | `/ws?ticket=<t>[&cid=<c>]` | 建立 WebSocket 連線（需先換 ticket）|

**Client 控制訊息：** `subscribe` / `unsubscribe` / `pong`  
**Server 推播訊息：** `welcome` / `ping` / `subscribed` / `unsubscribed` / `error` + 業務 topic

### 監控（`/monitoring`）

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET  | `/monitoring/logs` | SUPER_ADMIN | 查詢 Redis Stream 日誌（level/request_id/logger 篩選、cursor 分頁）|
| GET  | `/monitoring/db` | VIEWER+ | 即時 DB 連線池快照 |
| GET  | `/monitoring/db/history` | VIEWER+ | DB 指標時序歷史（Sorted Set 折線圖）|
| GET  | `/monitoring/metrics/{name}` | VIEWER+ | 指定 Stream 指標分頁查詢 |
| GET  | `/monitoring/infra` | VIEWER+ | OS / MySQL 基礎設施指標歷史 |

### 健康檢查（`/health`）

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET  | `/health` | 服務存活確認（回傳 app 版本）|
| GET  | `/health/db` | DB 連線（`SELECT 1`）|
| GET  | `/health/redis` | Redis 連線（`PING`）|
| GET  | `/health/node-exporter` | node-exporter 可達性 |
| GET  | `/health/mysqld-exporter` | mysqld-exporter 可達性 |

---

## 權限系統（RBAC）

### Admin 角色階梯

| 值 | 名稱 | 說明 |
|----|------|------|
| 0 | `VIEWER` | 唯讀（預設，fail-safe）|
| 50 | `EDITOR` | 可新增、修改、刪除資料 |
| 100 | `SUPER_ADMIN` | 全權，含管理其他 Admin |
| 999 | `ROOT` | Bootstrap root（`is_protected`；不可透過 API 降級或停用）|

### User 等級

| 值 | 名稱 |
|----|------|
| 0 | `FREE` |
| 5 | `PREMIUM` |

---

## 安全機制

| 機制 | 實作 |
|------|------|
| 密碼雜湊 | Argon2id（透過 threadpool 卸載，不阻塞 event loop）|
| Access Token | JWT / HS256，預設 30 分鐘 |
| Refresh Token | Opaque token + HMAC-SHA256 pepper；Rotation + Family 撤銷 |
| 欄位加密 | AES-256-CBC Deterministic（email 欄，可建唯一索引）|
| WS 防跨站劫持 | Origin 白名單（`WS_ALLOWED_ORIGINS`）|
| WS 速率限制 | 每連線每 10 秒控制訊息上限（`WS_CONTROL_MSG_RATE_LIMIT`）|
| WS 連線上限 | per-principal + 全實例雙重防 DoS |
| 統一錯誤格式 | 全域 exception handler；每則回應帶 `request_id` |
| Request ID | `RequestIdMiddleware`（全鏈路追蹤）|

---

## 開發指令

```bash
# 安裝
uv sync --dev

# 啟動（本機，含熱重載）
uv run uvicorn app.main:app --reload

# 測試
uv run pytest                          # 全部
uv run pytest tests/unit -v            # 只跑 unit
uv run pytest tests/integration -v     # 只跑 integration
uv run pytest -k <關鍵字>              # 篩選測試名稱
uv run pytest -x                       # 遇第一個失敗即停

# 品質檢查
uv run ruff check .                    # Lint
uv run ruff format .                   # 格式化
uv run ruff format --check .           # 只檢查不改
uv run pyright                         # 靜態型別

# 資料庫 Migration
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "描述"
uv run alembic downgrade -1
uv run alembic current
uv run alembic history
```

### 提交前完整檢查（對齊 CI）

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

---

## 測試架構

測試不依賴任何外部服務：

| 依賴 | 測試替代 |
|------|---------|
| MariaDB | SQLite in-memory（`APP_ENV=test` 自動切換）|
| Redis | `fakeredis` |

每個測試取得獨立 DB session，結束後自動 rollback，彼此完全隔離。

```
tests/
├── conftest.py          # fixtures：engine, db_session, client, alice, bob, fake_redis, cache
├── payloads.py          # 共用測試資料
├── data/                # 測試用使用者資料
├── unit/
│   ├── test_*.py                        # JWT、密碼、加密、安全、enum、模型等
│   ├── repositories/                    # admin, identity, principal, record, record_category, refresh_token, user
│   ├── services/
│   │   ├── test_auth*.py                # 認證、refresh、sid
│   │   ├── test_admin*.py               # admin service、RBAC
│   │   ├── test_record.py               # 記錄 service
│   │   ├── test_user.py
│   │   ├── monitoring/                  # db_probe, infra_probe, infra_sampler, log_handler, logs, sampler, store
│   │   └── ws/                          # bridge, manager, protocol, reauth, ticket, envelope
│   └── realtime/                        # streamer, realtime_reading_repo
└── integration/
    ├── test_auth_api.py
    ├── test_admin_auth_api.py
    ├── test_admin_management_api.py
    ├── test_records_api.py
    ├── test_realtime_history_api.py
    ├── test_realtime_stream_ws.py
    ├── test_refresh_api.py
    ├── test_refresh_reuse_commit.py
    ├── test_ws_*.py                     # handshake, heartbeat, control, dedup, limits, publish, revocation, shutdown, ticket
    └── test_monitoring_*.py             # db, db_history, infra, logs
```

---

## 專案結構

```
app/
├── main.py                 # ASGI 進入點（app.main:app）
├── app.py                  # create_app()、lifespan（startup / shutdown）
├── api/
│   ├── routers/
│   │   ├── auth/           # POST /auth/*
│   │   ├── admin/          # /admin/auth/*、/admin/admins/* CRUD
│   │   ├── records/        # CRUD /records/*
│   │   ├── realtime/       # GET /realtime/history
│   │   ├── ws/             # POST /ws/ticket、WS /ws
│   │   ├── monitoring/     # GET /monitoring/*
│   │   ├── health/         # GET /health/*
│   │   └── users/          # GET/PATCH/DELETE /users/*（User 自助）
│   ├── dependencies/       # DI：db session、redis、current_user、services
│   └── middlewares/        # RequestIdMiddleware
├── core/
│   ├── auth/               # JWT（jwt.py）、Argon2（password.py）、refresh token util
│   ├── config/             # 分環境設定（base / dev / local / prod / test）
│   ├── db/                 # engine、session、加密欄位型別（types.py）
│   ├── redis/              # 連線（session.py）、快取操作（cache.py）
│   ├── exceptions/         # 自訂例外（base / record）、全域 handler
│   ├── enums.py            # AdminRole、UserTier、Role、AppEnv 等
│   ├── context.py          # request_id context var
│   ├── logging.py          # 結構化 logging 設定
│   └── security.py         # PII 遮蔽、帳密政策
├── models/
│   ├── principal.py        # Principal（父表；role 型別判別子）
│   ├── user.py             # User（email AES-256 加密）
│   ├── admin.py            # Admin（argon2id hash、admin_role、封存 / 軟刪）
│   ├── identity.py         # Identity（OAuth / password 多身分）
│   ├── refresh_token.py    # RefreshToken（family_id 輪替鏈）
│   ├── record.py           # Record（業務記錄、軟刪除）
│   ├── record_category.py  # RecordCategory
│   └── realtime_reading.py # RealtimeReading（即時讀值、append-only）
├── repositories/           # BaseRepository + 各 repo（admin/user/identity/record 等）
├── services/
│   ├── auth.py             # AuthService（register / login / refresh / logout）
│   ├── admin.py            # AdminService（CRUD、生命週期、升降級、稽核）
│   ├── user.py             # UserService
│   ├── record.py           # RecordService（含批次匯入）
│   ├── initial_admin.py    # Bootstrap root upsert（startup 時執行）
│   ├── monitoring/         # DB 探針、日誌查詢、Infra 採樣、Redis Streams、leader lease
│   ├── realtime/           # RealtimeStreamer（背景生成）、歷史查詢
│   └── ws/                 # ConnectionManager、WsBridge、Pub-Sub、心跳、重認證、ticket
└── dtos/                   # 跨層 Pydantic DTO（auth / user / record / monitoring / ws）

alembic/versions/           # 20 個 DB migration（由工具自動產生，勿手改風格）
docs/
├── decisions/              # 架構決策記錄（Argon2 GIL、JWT 設計、RBAC、加密 IV、Refresh Token 等）
└── specs/                  # 功能規格文件（WebSocket、Monitoring、Records、Realtime 等）
tests/                      # unit / integration / smoke
```

---

## Lifespan 流程（啟動 / 關閉）

**啟動順序：**

1. **Bootstrap root** — `ensure_initial_admin()` 依 `INITIAL_ADMIN_*` env upsert DB admin 列
2. **WS Bridge** — Redis Pub-Sub → WebSocket 推播橋接
3. **Monitoring** — Redis Stream log handler + DB 狀態採樣器（`MONITORING_ENABLED=true`）
4. **Realtime Streamer** — 模擬資料生成 + 寫入 DB（`REALTIME_STREAM_ENABLED=true`）
5. **Infra Sampler** — node-exporter / mysqld-exporter 採樣（非 test 環境）

**關閉順序：**

Realtime Streamer → Infra Sampler → Monitoring Sampler → WS 優雅斷線 → DB engine dispose → Redis 關閉

---

## CI / CD

`.github/workflows/ci.yml` 在每次 push / PR 自動執行：

```
ruff check  →  ruff format --check  →  pyright  →  pytest
```

CI 測試環境注入 `APP_ENV=test`，使用 SQLite in-memory，**不需任何外部服務**。

---

## 延伸文件

`docs/` 目錄包含：

**`decisions/`（設計決策）**

| 文件 | 主題 |
|------|------|
| `argon2-gil.md` | Argon2 GIL / threadpool 卸載 |
| `exceptions.md` | 例外處理架構 |
| `identity-constraints.md` | 多身分 unique 約束 |
| `jwt-auth-fastapi-vs-flask.md` | FastAPI Bearer 認證差異 |
| `jwt-role-and-admin.md` | role 型別判別子設計 |
| `logging.md` | 結構化日誌與 request_id |
| `rbac.md` | RBAC 階梯設計（rank = value）|
| `redis-keys-scan.md` | Redis SCAN 注意事項 |
| `refresh-token-rotation.md` | Token 輪替與 reuse detection |
| `salt-and-iv.md` | AES-256 Salt / IV 策略 |

**`specs/`（功能規格書）**

| 文件 | 主題 |
|------|------|
| `admin-management-*.md` | Admin CRUD 模型 / Service / API |
| `bootstrap-hidden-admin.md` | Bootstrap root 機制 |
| `enum-int.md` | IntEnum rank = value 設計 |
| `infra-monitoring.md` | 基礎設施指標採集 |
| `jwt-role-and-admin.md` | JWT role / grade claim |
| `monitoring.md` | 監控整體架構 |
| `rbac.md` | RBAC 完整規格 |
| `realtime-*.md` | 即時串流 / 歷史查詢 |
| `records-*.md` | 資料記錄模型 / Service / API |
| `refresh-token-rotation.md` | Refresh token 完整規格 |
| `websocket.md` | WebSocket 完整規格（握手、心跳、限制、close code）|
