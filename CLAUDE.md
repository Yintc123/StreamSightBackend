# CLAUDE.md

本檔案為 Claude Code 在此 repo 工作時的指引。**所有開發一律嚴格遵守 TDD（測試驅動開發）。**

> **語言**：一律用繁體中文回答。

## 專案概觀

FastAPI 非同步後端範本（StreamSight Backend）。分層架構：**API → Services → Repositories → Models**，搭配 JWT 認證、Argon2 密碼雜湊、AES-256 欄位級加密、多身分（User / Identity）設計、Redis 快取。

- 語言：Python 3.13+ ／ 套件管理：`uv`
- Web：FastAPI + Uvicorn ／ ORM：SQLAlchemy 2.x（async）／ Migration：Alembic
- DB：PostgreSQL（asyncpg），測試時自動改用 SQLite in-memory（aiosqlite）
- 快取：Redis（測試以 `fakeredis` 模擬）
- 品質工具：`ruff`（lint + format）、`pyright`（型別檢查）

---

## ⚠️ 開發模式：嚴格 TDD（不可跳過）

**任何功能程式碼的變更，都必須先有一個失敗的測試。** 這是本專案不可協商的規則。

### Red → Green → Refactor 循環

對每一個行為（不是每一個檔案）重複以下步驟：

1. **RED — 先寫測試，並確認它會失敗**
   - 在寫任何實作前，先寫一個能表達「期望行為」的測試。
   - 執行測試，**親眼確認它因為正確的理由失敗**（不是 import error、不是打錯字）。
   - 若測試沒有失敗，代表測試無效或功能已存在——停下來釐清。

2. **GREEN — 寫剛好能讓測試通過的最少程式碼**
   - 只寫足以讓當前失敗測試變綠的實作，不要提前實作未被測試涵蓋的功能。
   - 執行測試，確認由紅轉綠，且未弄壞其他既有測試。

3. **REFACTOR — 在綠燈保護下重構**
   - 測試全綠後才整理程式碼（命名、去重、抽層），每次重構後都重跑測試維持全綠。

### 對 Claude 的硬性要求

- **禁止**在沒有對應失敗測試的情況下新增或修改業務邏輯。若使用者要求直接寫實作，先提出「我會先補一個失敗測試」，除非使用者明確要求跳過。
- 一次只推進一個小步驟：一個測試 → 一段實作 → 跑測試。不要一次寫一大段實作再補測試。
- 每個步驟都要**實際執行** `uv run pytest` 並回報結果，不能只憑推理宣稱通過。
- Bug 修復同樣走 TDD：**先寫一個能重現 bug 的失敗測試**，再修到它變綠（回歸測試）。
- 完成一項工作前，必須跑過完整檢查（見下方「提交前檢查」）並全數通過。

### 測試分層與放置位置

- `tests/unit/` — 單元測試，隔離單一 service／repository／util 的邏輯。
- `tests/integration/` — 透過 `httpx` ASGI client 打真實 API 端點的整合測試。
- 新增 API 端點 → 需有 integration 測試；新增 service／repository 邏輯 → 需有 unit 測試。
- 善用 `tests/conftest.py` 既有 fixtures：`engine`、`db_session`、`client`、`alice`、`bob`、`sample_users`、`fake_redis`、`cache`。共用測試資料放 `tests/payloads.py`。
- 測試特性：每個測試獨立 DB session、結束自動 rollback 互相隔離；Redis 用 `fakeredis`；`APP_ENV=test` 自動用 SQLite in-memory，**不需外部服務**。

---

## 常用指令

```bash
# 安裝
uv sync --dev

# 測試（TDD 主要迴圈）
uv run pytest                                   # 全部測試
uv run pytest tests/unit -v                     # 只跑 unit
uv run pytest tests/integration -v              # 只跑 integration
uv run pytest tests/integration/test_auth_api.py -v
uv run pytest -k <關鍵字>                        # 只跑符合名稱的測試（RED 階段常用）
uv run pytest -x                                # 遇第一個失敗即停

# 品質檢查
uv run ruff check .                             # Lint
uv run ruff format .                            # 格式化（加 --check 只檢查不改）
uv run pyright                                  # 靜態型別檢查

# 資料庫 Migration
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "message"
uv run alembic downgrade -1
```

### 提交前檢查（全數需通過，對齊 CI）

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

CI（`.github/workflows/ci.yml`）在 push / PR 時執行以上檢查。

---

## 專案結構

```
app/
├── main.py                 # ASGI 進入點 (app.main:app)
├── app.py                  # create_app()、lifespan
├── api/
│   ├── routers/            # auth / users / health / admin / public 路由
│   ├── dependencies/       # DI：db session、redis、current_user、services
│   └── middlewares/        # RequestIdMiddleware
├── core/                   # config / auth(JWT, Argon2) / db(engine, 加密欄位) / redis / exceptions / logging
├── models/                 # SQLAlchemy ORM（User / Identity）
├── repositories/           # 資料存取層（BaseRepository + 各 repo）
├── services/               # 商業邏輯（AuthService / UserService）
└── dtos/                   # 跨層傳遞的 Pydantic DTO

alembic/                    # DB migration（versions 由工具產生，勿手改風格）
tests/                      # unit / integration 測試
docs/                       # 主題文件（加密、JWT、logging、Redis 等）
```

新功能通常會同時觸及多層：**先在對應層寫失敗測試 → 由內而外（repository → service → API）逐層以 TDD 補齊。**

---

## 慣例與注意事項

- **分層職責**：API 層只做輸入驗證與 DI；商業邏輯放 Services；DB 存取放 Repositories。不要跨層直接呼叫（例如 router 直接碰 model）。
- **全非同步**：所有 DB／Redis 呼叫使用 `async/await`，勿引入阻塞呼叫。
- **例外處理**：使用 `app/core/exceptions` 的自訂例外，由全域 handler 統一回傳帶 `request_id` 的 JSON，不要在 router 內散落 try/except 直接回應。
- **加密金鑰**：`ENCRYPTION_KEY`、`JWT_SECRET_KEY` 至少 32 字元；`ENCRYPTION_KEY` 一旦有資料寫入即不可更改。
- **模型變更**：改動 `app/models` 後需以 Alembic 產生對應 migration。
- **風格**：Ruff line-length 100、double quote，啟用 `E/F/I/UP/B/SIM`；Pyright `standard` 模式，缺 import 視為 error。

---

## 黃金守則

> 沒有先失敗的測試，就不寫功能程式碼。
> Red 一定要親眼看到，Green 只寫最少實作，Refactor 只在全綠時進行。
