# 規格書：Refresh Token 模組（含 Rotation 機制）

> 狀態：Draft ／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 關鍵設計決策與取捨（為什麼這樣設計）另記於 [`../decisions/refresh-token-rotation.md`](../decisions/refresh-token-rotation.md)。本文聚焦「怎麼做」（資料模型／介面／流程／測試計畫）。

---

## 1. 背景與目標

目前系統只發放 **access token**（短效 JWT，預設 1800 秒）。access token 過期後使用者必須重新輸入帳密登入，體驗差；若把 access token 效期拉長，被竊取時風險又太大。

本模組引入 **refresh token**：長效憑證，讓 client 在 access token 過期後，用 refresh token 換取新的 access token，而不需重新登入。並加上 **rotation（輪替）** 與 **reuse detection（重用偵測）**，在提升體驗的同時控制被竊風險。

### 目標

- `login` / `register` 除了回傳 access token，另發放一組 refresh token。
- 新增 `POST /auth/refresh`：以 refresh token 換取「新的 access token + 新的 refresh token」。
- 新增 `POST /auth/logout`：撤銷（revoke）當前 refresh token。
- 新增 `POST /auth/logout-all`：撤銷該登入 user 的**所有** active refresh token（登出所有裝置）。
- **Rotation**：每次 refresh 都作廢舊 token、發新 token（單次使用，one-time use）。
- **Reuse detection**：偵測到已作廢的 refresh token 再次被使用，視為外洩，撤銷整條 token family（該次登入 session 的所有 refresh token）。

### 傳遞方式（已定案）

- refresh token 一律走 **response body**（JSON），**不使用 cookie / session** — 本 API server 維持無狀態、與現行 JWT access token 相同的傳遞模型。client 自行保存 refresh token，並在呼叫 `/auth/refresh`、`/auth/logout` 時放進 request body。

### 非目標（Out of scope）

- OAuth（Google / GitHub）的 refresh flow — 未來另立規格。
- Cookie / session 傳遞機制 — 明確不採用（見上）。
- Access token 的黑名單／即時撤銷 — access token 仍靠短效期自然過期。

---

## 2. 設計決策

### 2.1 Refresh token 是 opaque（不透明隨機字串），不是 JWT

**本設計的 refresh token 是 opaque token**——一段高強度隨機字串（`secrets.token_urlsafe(32)`，≈256 bits 熵），對 client 而言沒有可解析的內部結構，伺服器端靠「查 DB」來判斷其有效性。**它不是 JWT。**

對照本專案的兩種 token：

| | Access token | Refresh token |
|---|---|---|
| 型態 | **JWT**（自帶簽章的 claims） | **Opaque**（隨機字串，DB 為真實來源） |
| 驗證方式 | 無狀態：驗簽 + 檢查 `exp`，不查 DB | 有狀態：查 `refresh_tokens` 表比對 hash 與狀態 |
| 效期 | 短（預設 1800s） | 長（預設 14d） |
| 可否即時撤銷 | 否（靠短效期自然過期） | **是**（改 DB `revoked_at` 立即失效） |

**為什麼 refresh token 要用 opaque 而非 JWT？** refresh token 長效，一旦外洩風險高，必須能被伺服器**即時撤銷**（rotation、logout、reuse 連坐、logout-all 都依賴這點）。JWT 是無狀態自證的——簽出去後在到期前無法作廢，除非額外維護黑名單（等於又回到「查 DB」，失去 JWT 的意義）。反之 opaque token 的真實來源就是 DB，撤銷＝改一個欄位，天然支援上述所有機制。access token 則刻意維持 JWT + 短效期，換取「驗證不需查 DB」的高效能。**兩者職責分離：access 追求無狀態效能，refresh 追求可控可撤銷。**

### 2.2 DB 只存 token 的 keyed hash（HMAC-SHA256），不存明文

- token 明文只在發放當下回給 client，伺服器**不保存明文**；DB 只存其雜湊，對齊本專案「credential 不落地明文」原則。
- 採 **HMAC-SHA256（keyed hash，帶 server-side pepper）** 而非裸 SHA-256：即使 DB 單獨外洩，攻擊者缺少 pepper 也無法離線建表反查／偽造 lookup。pepper 由新設定 `refresh_token_hash_secret`（`SecretStr`，≥32 字元）提供，與 `jwt_secret_key` 分離（雜湊金鑰與簽章金鑰職責不同、不共用）。
- token 本身已是高熵隨機值，不需 argon2 這類慢雜湊（那是為了防「低熵密碼」被暴力破解）；HMAC-SHA256 快且足夠。輸出仍為 64 字元 hex，storage 不變。

### 2.3 儲存位置：新增 `refresh_tokens` 表（DB），而非 `identities`、也不用 Redis

**為何用 DB 而非本專案已有的 Redis？** refresh token 需要「依 `family_id` 連坐撤銷」與「依 `user_id` 全撤（logout-all）」這類**關聯查詢**，且 14 天長效憑證需要**持久性**（不能因 Redis flush/重啟就讓所有人被登出），rotation 又需與撤銷在**同一交易**中一致完成。這些用 DB 一次到位；若放 Redis 得自行維護 `family→tokens`、`user→tokens` 兩組二級索引集合、處理原子性與持久化，複雜且脆弱。故 refresh token 落 **DB**。（Redis 仍適合放「短效、可重建、無關聯」的 cache，與此情境不同。）

**為何不塞進 `identities` 表？** `identities` 是「登入方式」，一個 (user, provider) 一列且需長存；refresh token 是「可多筆、會過期、會輪替」的 session 憑證，語意不同。獨立成表較清晰。

### 2.4 Rotation ＋ Token Family（reuse detection）

- 每次成功 refresh：**撤銷**呈上的舊 token，**新增**一筆新 token，兩者屬同一 `family_id`（代表同一次登入 session）；並以 `replaced_by_id` 串起輪替鏈（audit 用）。
- `family_id` 在 `login` / `register` 首次發 token 時產生，之後同一 session 的所有輪替共用。
- **Reuse detection**：若呈上的 refresh token「存在但已被撤銷」，代表一個本應失效的舊 token 又被拿來用 → 判定外洩 → **撤銷整個 family**（所有屬於該 family 且尚未撤銷者）並回 401。攻擊者即使搶先用了偷來的 token，一旦真正使用者再輪替，就觸發整條 family 失效。

### 2.5 Rotation 的原子性（避免並發重複發 token）

「讀出舊 token → 標記 revoked → 發新 token」若是先讀後寫（read-then-write），在正式 DB（Postgres）上會有 **race condition**：同一個 R1 被並發送出兩次（前端重試、雙擊）時，兩個請求都可能讀到 R1 為 active → 各自發一個 child token，rotation 鏈分岔、reuse detection 失準。

**解法**：把「消費舊 token」做成**單一條件式原子 UPDATE**——
`UPDATE refresh_tokens SET revoked_at=:now, replaced_by_id=... WHERE id=:id AND revoked_at IS NULL`，
檢查 `rowcount == 1`：只有搶到的請求繼續發新 token；`rowcount == 0` 代表已被別人消費 → 走「已撤銷」處理路徑（見 2.6）。這讓 rotation 的「消費」步驟具備原子性與冪等性。

### 2.6 Reuse 誤判（false positive）與 grace 視窗

Rotation 有一個**已知取捨**：若合法 client 的 refresh 回應在網路上遺失、client 用同一個舊 token **重試**，該 token 此時已被撤銷 → 命中 reuse 路徑 → 可能誤把整條 session 連坐登出。

緩解：引入 **grace 視窗**（設定 `refresh_token_reuse_grace_seconds`，預設 10 秒）。當呈上的已撤銷 token 是在 `now - revoked_at <= grace` 內剛被撤銷（典型的並發／重試），視為良性 → **只回 401、不撤銷 family**（不連坐）；超過 grace 才判定為真正的外洩重用 → 撤銷整個 family。這在「安全」與「避免誤殺正常使用者」間取得平衡。無法安全地「重發同一個 child」（伺服器不留 child 明文），故 grace 的行為是「不連坐」而非「補發」。

---

## 3. 資料模型

新增 model：`app/models/refresh_token.py`，繼承 `Base`（自帶 `id` / `created_at` / `updated_at`）。

```python
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # HMAC-SHA256(pepper, plaintext) 的 hex digest；查詢與唯一性都靠它（見 2.2）
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 同一次登入 session 的輪替鏈共用同一 family_id（reuse detection 用）
    family_id: Mapped[str] = mapped_column(String(36), index=True)  # str(uuid4())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 撤銷時間；NULL = 仍有效。rotation / logout / reuse 撤銷都寫這裡
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 輪替鏈：本 token 被哪一筆新 token 取代（audit / debug 用），發放時為 NULL
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
```

**有效（active）token 判定**：`revoked_at IS NULL` 且 `expires_at > now`。

> 索引：`token_hash`（unique，查詢主鍵）、`user_id`、`family_id` 皆建 index，對應 `get_by_hash` / `revoke_all_for_user` / `revoke_family` 三種查詢路徑。

> **⚠️ Alembic 偵測前提**：必須在 `app/models/__init__.py` 加 `from .refresh_token import RefreshToken` 並列入 `__all__`（該檔註解已說明「models imported here for Alembic autogenerate to detect them」）。**漏掉這步，autogenerate 不會產生 `refresh_tokens` 表。**
>
> Migration：改動 model 後以 `uv run alembic revision --autogenerate -m "add refresh_tokens table"` 產生，再 `uv run alembic upgrade head`；產出的 migration 檔需人工檢視（欄位型別、index、unique 是否正確）。

---

## 4. 設定（Config）

於 `app/core/config/base.py` 的 `BaseAppSettings` 新增（時間單位一律「秒」，對齊現有慣例）。

> **命名注意**：refresh token 是 opaque、**不是 JWT**，故設定**不加 `jwt_` 前綴**（既有 `jwt_access_token_expire_seconds` 用 `jwt_` 是因為那確實是 JWT）。

```python
# refresh token 效期（opaque token，非 JWT）
refresh_token_expire_seconds: int = Field(
    default=1209600,           # 14 天
    ge=1,
    le=7776000,                # 上限 90 天
    description="Refresh token expiry in seconds (default 14d, max 90d)",
)
# refresh token 雜湊用的 pepper（HMAC-SHA256 key），與 jwt_secret_key 分離（見 2.2）
refresh_token_hash_secret: SecretStr = Field(
    default=SecretStr(""),
    description="Server-side pepper for HMAC-hashing refresh tokens (>=32 chars; NEVER expose)",
)
# reuse 誤判緩解視窗：剛撤銷 N 秒內的重放視為良性並發/重試，只 401 不連坐 family（見 2.6）
refresh_token_reuse_grace_seconds: int = Field(
    default=10,
    ge=0,
    le=300,
    description="Grace window (seconds) where re-presenting a just-rotated token does not nuke the family",
)
```

> `.env.example` 需同步補上 `REFRESH_TOKEN_HASH_SECRET`（≥32 字元）；`TestAppSettings` 可沿用預設，但 pepper 在測試需有值 → 於 `tests/conftest.py` import 前設 `os.environ["REFRESH_TOKEN_HASH_SECRET"]`（比照既有 `ENCRYPTION_KEY` / `JWT_SECRET_KEY` 的做法）。

---

## 5. 介面設計

### 5.1 核心工具 `app/core/auth/refresh.py`

無狀態的純函式，方便單獨單元測試：

- `generate_refresh_token() -> str`：回傳 `secrets.token_urlsafe(32)` 明文（≈256 bits 熵）。
- `hash_refresh_token(token: str) -> str`：回傳 **HMAC-SHA256(key=pepper, msg=token)** 的 hex digest（64 字元）。pepper 讀自 `get_app_settings().refresh_token_hash_secret`（比照 `jwt.py` 於函式內讀 settings 的慣例）。用 `hmac.compare_digest` 語意不適用於此（我們是產 hash 存 DB、以 hash 查詢），但**比對交給 DB 的 unique 查詢**，不在應用層做字串比較。

於 `app/core/auth/__init__.py` 匯出。

### 5.2 Repository `app/repositories/refresh_token.py`

`RefreshTokenRepository(BaseRepository[RefreshToken])`：

- `get_by_hash(token_hash: str) -> RefreshToken | None`
- `consume(token_id: int, revoked_at: datetime, replaced_by_id: int | None = None) -> bool`：**原子式消費**（見 2.5）。執行 `UPDATE ... SET revoked_at=:t, replaced_by_id=:r WHERE id=:id AND revoked_at IS NULL`，回傳 `rowcount == 1`（是否搶到）。rotation 的核心防並發步驟。
- `revoke_family(family_id: str, revoked_at: datetime) -> int`：撤銷該 family 下所有 `revoked_at IS NULL` 的列，回傳撤銷筆數（reuse detection 用）。
- `revoke_all_for_user(user_id: int, revoked_at: datetime) -> int`：撤銷該 user 所有 `revoked_at IS NULL` 的列，回傳撤銷筆數（登出所有裝置用）。
- `delete_expired(before: datetime) -> int`：刪除 `expires_at <= before` 的列，回傳刪除筆數（清理用，見 §7.1）。

（撤銷用 `UPDATE`（`CursorResult.rowcount`），比對 `BaseRepository.delete_by_id` 的寫法。）需在 `app/repositories/__init__.py` 匯出 `RefreshTokenRepository`。

### 5.3 DTO `app/dtos/auth.py`

- `TokenPayload` 擴充：新增 `refresh_token: str | None = None`（domain contract；沿用既有「API 層再擴充」註解）。
- 新增 `RefreshRequest(BaseModel)`：`refresh_token: str`。
- 於 `app/dtos/__init__.py` 匯出 `RefreshRequest` 並列入 `__all__`。
- ✅ 相容性：既有整合測試以 `assert "access_token" in data` 檢查（非精確鍵比對），新增 `refresh_token` 欄位**不會**弄壞既有測試。

### 5.4 API schema `app/api/routers/auth/schemas.py`

- `TokenResponse` 繼承 `TokenPayload`（自動帶 `access_token` / `token_type` / `refresh_token`），**在此新增 API 專屬欄位 `expires_in: int`**（access token 剩餘有效秒數，OAuth2 慣例；值取 `settings.jwt_access_token_expire_seconds`）。這正是既有 schemas.py 註解所指「API-specific 欄位（例如 expires_in）」的落點，維持 domain `TokenPayload` 乾淨。
- `RefreshRequest` 直接複用 `app/dtos` 的 DTO 當 request body，router import 即可。
- `expires_in` 由 router 在建 `TokenResponse` 時填入（見 5.6），service 層不需知道它。

### 5.5 Service `app/services/auth.py`

> **Transaction 契約（重要）**：沿用 `UserService` 的既有慣例——「每個 public method 自行 `commit`（或 rollback on error）」（見 `app/services/user.py:19`）。以下每個會寫 DB 的方法都必須明確 `await self.session.commit()`，寫入才會落地。**特別注意 `get_session` 依賴在遇到例外時會 `rollback`**（`app/api/dependencies/db.py`），因此任何「先寫入、再拋例外」的路徑（見 `refresh` 的 reuse detection）**必須在 `raise` 之前先 `commit`**，否則寫入會被 rollback 而形同未執行。

新增 / 調整方法：

- `_issue_refresh_token(user_id, family_id) -> tuple[str, RefreshToken]`：產明文 token（`generate_refresh_token`）→ 建 `RefreshToken`（`token_hash=hash_refresh_token(明文)` + family_id + `expires_at = now + refresh_token_expire_seconds`）→ `repo.add`（flush 後拿到 `id`）→ 回 `(明文, row)`。**呼叫端負責 commit**。回傳 row 是為了讓 rotation 能把舊 token 的 `replaced_by_id` 指向新 token。
- `register`：於現有 user commit → identity commit 之後，**新增第三段**：`family_id = str(uuid4())` → `refresh, _ = await self._issue_refresh_token(user.id, family_id)` → `await self.session.commit()` → 把 `refresh` 放進回傳的 `TokenPayload`。
  - 失敗處理：refresh 發放在 user/identity 都已 commit 之後；若此段失敗，rollback 只影響這筆 refresh token，user/identity 仍在（可接受——使用者仍可用 `/auth/login` 取得 refresh token，不需補償刪除）。
- `login`：verify 成功後，`family_id = str(uuid4())` → `refresh, _ = await self._issue_refresh_token(user.id, family_id)` → `await self.session.commit()` → 放進 `TokenPayload`。並在此低頻路徑 best-effort 呼叫 `repo.delete_expired(now)` 做 opportunistic 清理（見 §7.1；失敗不影響登入）。
- `refresh(payload: RefreshRequest) -> TokenPayload`：`now = datetime.now(UTC)`
  1. `h = hash_refresh_token(payload.refresh_token)` → `rt = await repo.get_by_hash(h)`。
  2. `rt is None` → `raise UnauthorizedError("Invalid refresh token")`（無寫入）。
  3. **已撤銷（`rt.revoked_at` 非 NULL）→ reuse 判定（含 grace，見 2.6）**：
     - 若 `now - rt.revoked_at <= grace_seconds`（剛輪替的良性並發/重試）→ `raise UnauthorizedError("Invalid refresh token")`（**只 401、不撤 family、無寫入**）。
     - 否則（真正的舊 token 重用）→ `await repo.revoke_family(rt.family_id, now)` → **`await self.session.commit()`（務必在 raise 前）** → `raise UnauthorizedError("Invalid refresh token")`。
  4. 已過期（`rt.expires_at <= now`）→ `raise UnauthorizedError("Refresh token expired")`（無寫入）。
  5. **先驗 user**：確認 user 仍存在且 `is_active`（`user_service.repo.get(rt.user_id)`）；否則 `raise UnauthorizedError`（無寫入——不對停用 user 發任何新 token）。刻意排在寫入前，避免留下孤兒 active token。
  6. **原子消費（見 2.5）**：先發新 token 取得其 id，再原子撤銷舊 token 並指向新 token：
     - `new_plain, new_row = await self._issue_refresh_token(rt.user_id, rt.family_id)`（flush 拿到 `new_row.id`）。
     - `won = await repo.consume(rt.id, revoked_at=now, replaced_by_id=new_row.id)`。
     - `won is False`（並發下已被別的請求消費）→ `await self.session.rollback()` → `raise UnauthorizedError("Invalid refresh token")`（rollback 一併撤掉剛才多發的 `new_row`，不留孤兒 token）。
  7. `await self.session.commit()` → `new_access = create_access_token(rt.user_id)` → 回 `TokenPayload(access_token=new_access, refresh_token=new_plain)`。
- `logout(payload: RefreshRequest) -> None`：hash → `get_by_hash` → 找到且未撤銷就設 `revoked_at = now` 並 `commit`；找不到則直接回（靜默成功，避免 enumeration，不需 commit）。
- `logout_all(user_id: int) -> None`：`await repo.revoke_all_for_user(user_id, now)` → `await self.session.commit()`。撤銷該 user 全部 active token（登出所有裝置）。

> 時間一律 `now = datetime.now(UTC)`，沿用 `app/core/auth/jwt.py` 慣例。`grace_seconds` 讀自 `settings.refresh_token_reuse_grace_seconds`。錯誤訊息刻意統一、模糊化，延續 `login` 防列舉（user enumeration）的既有做法。

### 5.6 Router `app/api/routers/auth/router.py`

- 既有 `register` / `login` 回傳的 `TokenResponse` 一併填 `expires_in=settings.jwt_access_token_expire_seconds`（建議抽一個小 helper `_to_token_response(token: TokenPayload) -> TokenResponse` 統一組裝，避免各端點重複）。
- `POST /auth/refresh` → `service.refresh(payload)` → `200` `TokenResponse`（含新 access / refresh / expires_in）。
- `POST /auth/logout` → `service.logout(payload)` → `204 No Content`。
- `POST /auth/logout-all` → 需登入（`get_current_user` 依賴，帶 `Authorization: Bearer <access>`）→ `service.logout_all(current_user.id)` → `204 No Content`。

---

## 6. 流程圖（Rotation ＋ Reuse detection）

```
login/register ──► 發 A(access) + R1(refresh, family=F)         [R1 active]

client 用 R1 refresh（原子消費）:
  consume(R1)=1 ──► R1.revoked, R1.replaced_by=R2、發 R2(family=F)+新 access

client（正常）再用 R2 refresh:
  consume(R2)=1 ──► R2.revoked, R2.replaced_by=R3、發 R3(family=F)

並發/網路重試：R1 回應遺失，client 立刻又送 R1（在 grace 內）:
  R1 已 revoked 且 (now - revoked_at) <= grace ──► 良性 ──► 401、不連坐 family
  ⇒ 正常使用者手上的最新 token 仍有效，不被誤殺

攻擊者偷到 R1，隔一段時間（超過 grace）再呈上 R1:
  R1 已 revoked 且 (now - revoked_at) > grace ──► REUSE! 撤 family F 全部 (R3…) ──► 401
  ⇒ 攻擊者與真正使用者都被踢出，需重新登入
```

---

## 7. 安全性考量

- refresh token 明文永不落 DB、永不寫入 log（log 只記 `user_id` 與遮罩後 email，沿用 `mask_email`）。
- token 高熵（`token_urlsafe(32)` ≈ 256 bits）；DB 存 **HMAC-SHA256(pepper, token)**，純 DB 外洩無法反查／偽造（見 2.2）。
- rotation 讓每個 refresh token 單次使用，縮短被竊 token 的可用視窗；「消費」為原子 UPDATE，防並發雙發（見 2.5）。
- reuse detection 讓「舊 token 再現」連坐整條 session，限制外洩影響；grace 視窗避免誤殺正常並發/重試（見 2.6）。
- 統一且模糊的錯誤訊息，避免 token / 帳號列舉。
- 過期／已撤銷 token 由 DB 狀態即時判定，不受 JWT 無狀態限制。

### 7.1 過期 token 清理（cleanup）

rotation 會持續累積列（14 天效期 + 頻繁輪替，單一活躍用戶可產生大量 revoked/expired 列），需有回收策略，否則 `refresh_tokens` 無界成長：

- **Opportunistic（本次採用）**：在**低頻**事件時順手清理，避免污染 refresh 熱路徑——於 `login` 成功後 best-effort 呼叫 `repo.delete_expired(now)`（刪 `expires_at <= now` 者，含已過期的 revoked 列）。清理失敗不影響登入（包在自身 try 或獨立 commit）。
- **Cron（未來）**：`delete_expired` 也可由排程/背景工作定期全表呼叫；本專案目前無排程器，故先走 opportunistic，保留此方法供未來接 cron。
- 仍在效期內的 revoked 列**不刪**（reuse detection 需要它們在 grace 之外仍可被辨識為「曾存在且已撤銷」）。

---

## 8. TDD 測試計畫（先寫、先看到 RED）

> 依 `CLAUDE.md`：**每個行為先寫一個失敗測試，確認因正確理由 RED，再寫最少實作轉 GREEN，全綠後才 refactor。** 由內而外（core → repository → service → API）逐層推進。

### 8.1 Unit — `tests/unit/test_refresh_token_util.py`
- `generate_refresh_token` 兩次呼叫結果不同、長度足夠、為 URL-safe 字串。
- `hash_refresh_token` 對同一輸入輸出穩定、對不同輸入不同、輸出為 64 字元 hex、且不等於明文。
- `hash_refresh_token` 是 **keyed**：改變 pepper（`refresh_token_hash_secret`）→ 同一 token 的 hash 改變（驗證 HMAC 而非裸 SHA-256；用 monkeypatch/清 `get_app_settings` cache 切換設定）。

### 8.2 Unit — `tests/unit/test_refresh_token_repository.py`
- `add` 後 `get_by_hash` 能取回；不存在的 hash 回 `None`。
- `consume(id)`：對 active token 回 `True` 且該列變 revoked、`replaced_by_id` 被設；**對已 revoked 的 token 回 `False` 且不覆寫**（原子性/冪等：模擬「第二個並發請求」搶不到）。
- `revoke_family` 只撤銷同 family 且尚未撤銷者，回正確筆數；不影響其他 family。
- `revoke_all_for_user` 只撤銷該 user 且尚未撤銷者，回正確筆數；不影響其他 user 與已撤銷者。
- `delete_expired(before)` 只刪 `expires_at <= before`，回正確筆數；未過期者保留。

### 8.3 Unit — `tests/unit/test_auth_service_refresh.py`
- `register` / `login` 回傳的 `TokenPayload` 帶非空 `refresh_token`，且 DB 有一筆對應 active token。
- `refresh(有效 token)`：回新 access 與新 refresh；舊 token 於 DB 變 revoked 且 `replaced_by_id` 指向新 token；新舊屬同一 family。
- `refresh(不存在 token)` → `UnauthorizedError`。
- `refresh(已過期 token)` → `UnauthorizedError("Refresh token expired")`。
- `refresh(已撤銷、超過 grace)` → reuse detection：同 family 其餘 active token 全被撤銷，且拋 `UnauthorizedError`。
- `refresh(已撤銷、在 grace 內)` → **只拋 `UnauthorizedError`、family 其餘 token 不受影響**（grace 良性路徑，見 2.6）。以直接寫入 `revoked_at = now`（grace 內）建構。
- `refresh(user 已停用/刪除)` → `UnauthorizedError`。
- `login` 後 DB 中已過期的舊列被 `delete_expired` 清掉（opportunistic 清理，見 §7.1）。
- `logout(有效 token)` → 該 token 變 revoked；之後再 `refresh` 該 token（超過 grace）→ 401。
- `logout(不存在 token)` → 不拋錯（靜默成功）。
- `logout_all(user_id)` → 該 user 所有 active token 全變 revoked；其他 user 的 token 不受影響。

### 8.4 Integration — `tests/integration/test_refresh_api.py`
- `login` 回應含 `refresh_token`，且含 `expires_in`（值 = `jwt_access_token_expire_seconds`）。
- `POST /auth/refresh` 帶有效 token → `200`，回新的 access + refresh（+ `expires_in`）；舊 refresh 再用（超過 grace）→ `401`。
- rotation 鏈：R1→R2→R3 連續 refresh 皆成功。
- reuse（超過 grace）：用已輪替過的舊 token → `401`，且事後連最新 token 也失效（family 連坐）。
- grace（在視窗內）：剛輪替後立即重放舊 token → `401`，但**最新 token 仍可繼續 refresh**（未連坐）。以極短 grace 直接驗證良性路徑。
- `POST /auth/logout` 帶有效 token → `204`；之後用該 token refresh → `401`。
- `POST /auth/logout-all`：以某 user 登入（帶 access token）連發多裝置 refresh token 後呼叫 → `204`，該 user 全部 refresh token 皆失效（逐一 refresh 皆 `401`）；未帶 access token → `401`。
- 「多裝置」以**多次 `POST /auth/login`** 模擬（每次 login 產生新 family + 新 refresh token）。
- 缺欄位／格式錯誤 → `422`。

> 沿用 `conftest.py` 既有 fixtures（`client`、`alice`、`db_session` 等）；時間相關測試直接寫入過期 `expires_at` / 已撤銷 `revoked_at` 的 `RefreshToken` 列來構造，避免 sleep。

### 8.5 Integration（production-faithful session）— reuse detection 的 commit-before-raise

> **為何需要獨立 harness**：既有 `client` fixture 讓所有請求共用同一個 `db_session`（同一 connection／transaction），且 override 版 `get_session` **不做** rollback-on-exception。因此 reuse detection 若漏了「commit 再 raise」，在共享 session 下仍看得到未提交的撤銷 → 8.4 的 reuse 測試會**誤判通過**。此 bug 只在正式環境（每請求獨立 session、`get_session` 遇例外 rollback）才爆發。

- 新增一個 **production-faithful** 的 `get_session` override（本檔 fixture 局部定義）：每次請求開新 session，並複製正式 `get_session` 的 `try/yield/except: rollback` 語意（可綁在同一測試 engine/connection 上，但每請求獨立 transaction）。
- 測試：觸發一次 reuse（呈上已撤銷的舊 token）→ 斷言回 `401`，**且在另一個獨立 session 查詢**確認該 family 的最新 token `revoked_at` 已寫入（撤銷已落地）。
- **預期 TDD 行為**：service 若「先 `revoke_family` 但未 commit 就 raise」，此測試會 RED（撤銷被 rollback）；補上 commit-before-raise 後轉 GREEN。這是驗證 Critical 修正的唯一可靠測試。

---

## 9. 實作順序（TDD 里程碑）

1. **config**：新增 `refresh_token_expire_seconds` / `refresh_token_hash_secret` / `refresh_token_reuse_grace_seconds`；`.env.example` 與 `conftest.py` 補 pepper。
2. **core/auth/refresh**：`generate_refresh_token` + `hash_refresh_token`（HMAC）+ `__init__` 匯出（8.1）。
3. **model + migration**：`RefreshToken` 表（含 `replaced_by_id` 自參考 FK）；**務必在 `app/models/__init__.py` import** → autogenerate → 人工檢視 migration → `upgrade head`。
4. **repository**：`RefreshTokenRepository`（`get_by_hash` / `consume` 原子 / `revoke_family` / `revoke_all_for_user` / `delete_expired`）+ `repositories/__init__` 匯出（8.2）。
5. **dto/schema**：`TokenPayload` 加 `refresh_token`、`RefreshRequest` + `dtos/__init__` 匯出、`TokenResponse` 加 `expires_in`。
6. **service**：`_issue_refresh_token` / `register`+`login`（含 opportunistic 清理）發 refresh / `refresh`（原子消費 + grace + commit-before-raise）/ `logout` / `logout_all`（8.3）。
7. **router**：`register`/`login` 填 `expires_in`、`/auth/refresh`、`/auth/logout`、`/auth/logout-all`（8.4）。
8. **並發/commit 驗證**：`consume` 原子性（8.2）、grace 良性路徑（8.3/8.4）、production-faithful reuse commit-before-raise（8.5）。
9. **提交前檢查**：`ruff check` / `ruff format --check` / `pyright` / `pytest` 全綠。

---

## 10. 已定案決策

- ✅ refresh token 是 **opaque 隨機字串（非 JWT）**，DB 存 **HMAC-SHA256(pepper)**（見 2.1 / 2.2）。
- ✅ 儲存於 **DB**（非 Redis、非 `identities`），理由見 2.3。
- ✅ refresh token 走 **response body**，不使用 cookie / session。
- ✅ Rotation「消費」為**原子 UPDATE**（見 2.5）；reuse detection 帶 **grace 視窗**（見 2.6）。
- ✅ Config 不加 `jwt_` 前綴：`refresh_token_expire_seconds` 等。
- ✅ 本次一併實作：`POST /auth/logout-all`、`replaced_by_id` 輪替鏈、`expires_in` 回應欄位、`delete_expired` opportunistic 清理。

## 11. 待確認事項（Open Questions）

1. 單一 user 是否限制同時存活的 family（裝置）數量上限？（暫定：不限制）
2. 未來是否接排程器把 `delete_expired` 改為 cron 全表清理（取代／補強 opportunistic）？（暫定：先 opportunistic）
3. grace 預設 10 秒是否符合前端重試行為？上線後可依實測調整。
```
