# FastAPI Foundation Template

一個 production-ready 的 FastAPI 專案樣板，示範以 **非同步 (async)** 架構打造安全、可維護的後端 API。內建 JWT 認證、Argon2 密碼雜湊、欄位級加密、多身分 (multi-identity) 設計、Redis 快取，以及完整的分層架構與測試。

適合作為新專案的起點，或作為 FastAPI 最佳實踐的參考實作。

---

## 特色

- **分層架構** — API → Services → Repositories → Models，職責清晰、易於測試。
- **全非同步** — Uvicorn + FastAPI + SQLAlchemy 2.x (async) + asyncpg / aiosqlite。
- **JWT 認證** — OAuth2 Bearer token，Argon2id 密碼雜湊（透過 threadpool 卸載，不阻塞 event loop）。
- **多身分設計** — `User` 與 `Identity` 分離，一位使用者可綁定多種登入方式（password / OAuth）。
- **欄位級加密** — `User.email` 以 AES-256 加密儲存（deterministic，可建索引與唯一鍵）。
- **統一例外處理** — 自訂例外類別 + 全域 handler，回傳一致的 JSON 錯誤格式並帶 `request_id`。
- **可觀測性** — 結構化 logging、Request ID middleware、health check（app / DB / Redis）。
- **多環境設定** — 以 `pydantic-settings` 依 `APP_ENV` 載入 local / dev / prod / test 設定。
- **完整測試** — pytest + pytest-asyncio，per-test DB 隔離（rollback），Redis 以 `fakeredis` 模擬。
- **現代工具鏈** — `uv` 套件管理、`ruff`（lint + format）、`pyright`（型別檢查）、Alembic migration、GitHub Actions CI。

---

## 技術棧

| 分類 | 技術 |
|------|------|
| 語言 | Python 3.13+ |
| Web 框架 | FastAPI 0.138+ / Uvicorn |
| 資料驗證 | Pydantic v2 / pydantic-settings |
| ORM | SQLAlchemy 2.x (async) |
| Migration | Alembic |
| 資料庫 | PostgreSQL (asyncpg) — 測試時自動改用 SQLite in-memory (aiosqlite) |
| 快取 | Redis (redis-py + hiredis) |
| 認證 | PyJWT / argon2-cffi |
| 加密 | cryptography (AES-256) |
| 測試 | pytest / pytest-asyncio / httpx / fakeredis |
| 工具 | uv / ruff / pyright |

---

## 專案結構

```
app/
├── main.py                 # ASGI 進入點 (app.main:app)
├── app.py                  # create_app()、lifespan
├── api/                    # API 層
│   ├── routers/            # auth / users / health 路由
│   ├── dependencies/       # DI：db session、redis、current_user、services
│   └── middlewares/        # RequestIdMiddleware
├── core/                   # 基礎設施
│   ├── config/             # 多環境設定 (base / local / dev / prod / test)
│   ├── auth/               # JWT 編解碼、Argon2 密碼雜湊
│   ├── db/                 # engine / session / DeclarativeBase / 加密欄位型別
│   ├── redis/              # 連線池與 cache wrapper
│   ├── exceptions/         # 自訂例外與全域 handler
│   └── logging.py          # 結構化 logging
├── models/                 # SQLAlchemy ORM 模型 (User / Identity)
├── repositories/           # 資料存取層 (BaseRepository + 各模型 repo)
├── services/               # 商業邏輯 (AuthService / UserService)
└── dtos/                   # 跨層傳遞的 Pydantic DTO

alembic/                    # 資料庫 migration
tests/                      # unit / integration 測試
docs/                       # specs/（功能規格書）＋ decisions/（設計決策與主題筆記）
docker-compose.yml          # 本機 PostgreSQL + Redis
```

---

## 快速開始

### 先決條件

- Python 3.13+
- [uv](https://github.com/astral-sh/uv)（套件管理）
- Docker + Docker Compose（本機啟動 PostgreSQL 與 Redis）

### 1. 安裝依賴

```bash
uv sync            # 安裝 production 依賴
uv sync --dev      # 連同開發／測試依賴一起安裝
```

### 2. 設定環境變數

複製範例檔並填入實際值：

```bash
cp .env.example .env
```

重點：`ENCRYPTION_KEY` 與 `JWT_SECRET_KEY` 皆需 **至少 32 字元**，正式環境請使用高強度隨機值（例如 `python -c "import secrets; print(secrets.token_urlsafe(64))"`）。

> ⚠️ `ENCRYPTION_KEY` 一旦有資料寫入就 **不可再更改**，否則既有加密欄位將無法解密。

### 3. 啟動本機依賴服務

```bash
docker compose up -d       # 啟動 PostgreSQL (5432) 與 Redis (6379)
```

### 4. 執行資料庫 migration

```bash
uv run alembic upgrade head
```

### 5. 啟動開發伺服器

```bash
uv run uvicorn app.main:app --reload
```

啟動後可存取：

- API 首頁健康檢查：http://localhost:8000/health
- Swagger UI：http://localhost:8000/docs
- ReDoc：http://localhost:8000/redoc

---

## 環境變數

| 變數 | 說明 | 範例／預設 |
|------|------|-----------|
| `APP_ENV` | 執行環境：`local` / `development` / `production` / `test` | `local` |
| `DB_DIALECT` | 資料庫 dialect + driver | `postgresql+asyncpg` |
| `DB_HOST` / `DB_PORT` | 資料庫位置 | `localhost` / `5432` |
| `DB_USER` / `DB_PASSWORD` | 資料庫帳密 | `postgres` / — |
| `DB_NAME` | 資料庫名稱 | `fastapi_template_local` |
| `REDIS_HOST` / `REDIS_PORT` | Redis 位置 | `localhost` / `6379` |
| `REDIS_USERNAME` / `REDIS_PASSWORD` | Redis 認證（留空＝不啟用） | — |
| `REDIS_DB` | Redis DB index | `0` |
| `ENCRYPTION_KEY` | 欄位加密金鑰（AES-256，≥32 字元，設定後勿更改） | — |
| `JWT_SECRET_KEY` | JWT 簽章密鑰（≥32 字元） | — |
| `JWT_ALGORITHM` | JWT 演算法 | `HS256` |
| `JWT_ACCESS_TOKEN_EXPIRY_SECONDS` | access token 有效期（秒，上限 24 小時） | `1800` |

> 測試環境 (`APP_ENV=test`) 會自動改用 SQLite in-memory，無需啟動 PostgreSQL。

---

## API 端點

### Health (`/health`)

| Method | Path | 說明 |
|--------|------|------|
| GET | `/health` | 基本健康檢查，回傳 app 版本 |
| GET | `/health/db` | 資料庫連線檢查（`SELECT 1`） |
| GET | `/health/redis` | Redis 連線檢查（`PING`） |
| GET | `/health/test-error/{kind}` | 例外處理 demo（`notfound` / `business` / `unhandled` / `other`），正式部署前應移除 |

### Auth (`/auth`)

| Method | Path | 說明 |
|--------|------|------|
| POST | `/auth/register` | 註冊新使用者並自動登入，回傳 access token（201） |
| POST | `/auth/login` | 以 email + 密碼登入，回傳 access token |

Token 回應格式：

```json
{ "access_token": "<jwt>", "token_type": "bearer" }
```

### Users (`/users`)

| Method | Path | 說明 |
|--------|------|------|
| POST | `/users` | 建立使用者（201） |
| GET | `/users` | 列出所有使用者 |
| GET | `/users/me` | 取得目前登入的使用者（需 `Authorization: Bearer <token>`） |
| GET | `/users/{user_id}` | 依 ID 取得使用者 |
| PATCH | `/users/{user_id}` | 部分更新使用者 |
| DELETE | `/users/{user_id}` | 刪除使用者（204） |

---

## 資料模型

- **User** (`users`) — `id`、`email`（AES-256 加密、唯一、可為 null 以支援 OAuth）、`name`、`is_active`、`created_at`、`updated_at`。
- **Identity** (`identities`) — `id`、`user_id`（FK，CASCADE）、`provider`（如 `password` / `google`）、`provider_user_id`、`credential`（password provider 存 argon2 hash）、時間戳。
  - 約束：`UNIQUE(user_id, provider)`、`UNIQUE(provider, provider_user_id)`。

此設計讓一位使用者可綁定多種登入方式，便於未來擴充 OAuth 供應商。

---

## 資料庫 Migration（Alembic）

```bash
uv run alembic upgrade head                          # 套用所有 migration
uv run alembic revision --autogenerate -m "message"  # 依模型變更自動產生 migration
uv run alembic downgrade -1                           # 回退一個版本
uv run alembic current                                # 顯示目前版本
uv run alembic history                                # 檢視歷史
```

---

## 測試

```bash
uv run pytest                                  # 執行全部測試
uv run pytest tests/unit -v                    # 只跑 unit 測試
uv run pytest tests/integration -v             # 只跑 integration 測試
uv run pytest tests/integration/test_auth_api.py -v
```

測試特點：

- 自動使用 `TestAppSettings`（SQLite in-memory），無需外部服務。
- 每個測試取得獨立的 DB session，結束後自動 rollback，彼此隔離。
- Redis 以 `fakeredis` 純記憶體模擬，測試不需真實 Redis。
- `conftest.py` 提供 `engine`、`db_session`、`client`、`alice`、`bob`、`sample_users`、`fake_redis`、`cache` 等 fixtures。

---

## 程式碼品質

```bash
uv run ruff check .            # Lint
uv run ruff format .           # 格式化（--check 只檢查不修改）
uv run pyright                 # 靜態型別檢查
```

設定重點（見 `pyproject.toml`）：

- **Ruff**：line-length 100，啟用 `E / F / I / UP / B / SIM` 規則，double quote。
- **Pyright**：`standard` 模式，涵蓋 `app/` 與 `tests/`，缺少 import 視為 error。

CI（GitHub Actions，`.github/workflows/ci.yml`）會在 push / PR 時執行 ruff lint、ruff format check、pyright 與 pytest。

---

## 延伸文件

`docs/` 分為兩類：`specs/`（功能規格書，描述「要做什麼／怎麼做」）與 `decisions/`（設計決策與主題筆記，記錄「為什麼這樣設計」）。

**規格書（`docs/specs/`）**

- `refresh-token-rotation.md` — Refresh Token 模組（含 rotation / reuse detection）規格

**設計決策與主題筆記（`docs/decisions/`）**

- `refresh-token-rotation.md` — Refresh Token 的關鍵設計決策與取捨
- `jwt-auth-fastapi-vs-flask.md` — JWT 認證流程與 FastAPI/Flask 差異
- `argon2-gil.md` — Argon2 密碼雜湊與 GIL/threadpool 考量
- `salt-and-iv.md` — 加密的 salt 與 IV
- `identity-constraints.md` — 多身分設計的約束
- `exceptions.md` — 例外處理設計
- `logging.md` — 結構化 logging
- `redis-keys-scan.md` — Redis key 掃描注意事項

---

## License

尚未指定。
