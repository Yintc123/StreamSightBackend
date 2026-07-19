# 設計書：初始 admin 落地為真實 DB 列（消滅哨兵模式）

> 狀態：**定稿（待實作）** ／ 開發模式：**嚴格 TDD** ／ 取代：`initial_admin.py` 的 SSM 哨兵（`principal_id=0`、synthetic Admin、`sub==0` 特判）
>
> 📎 動機：修 **C2**（初始 admin 執行 archive/delete → `archived_by/deleted_by=0` 撞 FK→`principals.id` → 500，已實測重現）**且保留稽核歸屬**（NULL-fold 會讓哨兵操作 `*_by=NULL`、與「被刪 admin 做的」混淆，不符預期）。同時解掉 **records `created_by` FK** 對初始 admin 的相容問題（免守衛）。

---

## 0. 一句話

初始/root admin 從「SSM 哨兵（不落 DB、每處特判）」改成**一筆開機時自動建立的普通 `super_admin` 真實 DB 列 + `is_protected=True`**。username/name 由 env、**初始密碼由 env 明文於啟動時 hash 後寫入 DB**（seed-once）。**登入、稽核、授權、改密碼全走一般路徑，零特判**——root 就是「一個開機自動建立、初始密碼來自 env 的普通 super_admin」。

**核心體悟（判準）**：哨兵模式存在的唯一理由是「希望 root 不進 DB」。**root 一旦進 DB，哨兵模式（synthetic admin / `principal_id=0` / `sub==0` 分支 / SSM 登入特判 / API 不可改密碼）就全部失去存在意義，應一併移除。**

---

## 1. 決策紀錄

| 候選 | 結果 | 取捨 |
|---|---|---|
| NULL-fold（`*_by = 0 → None`） | 500 消失 | ❌ 哨兵操作**無稽核歸屬**，與「被刪 admin 做的」混淆 |
| 種 `principals(id=0)` / 強制 id=0 | FK 滿足 | ❌ 撞 MariaDB `AUTO_INCREMENT`-on-zero；id=0 仍是特例 |
| Y：真列 + DB 存 SSM hash、登入驗 SSM | C2 解 | ⚠️ 保留一條登入特判；「hash 不落 DB」價值不高（DB 本就存 hash） |
| **X：真列 + 登入完全一般（採用）** | C2 解 + records 解 + **消滅所有特判** | ✅ 最符合「root 進 DB 就不用哨兵」 |

**採 X + seed-once + startup upsert；密碼契約用 env 明文（`initial_admin_password`），啟動時 hash。** id **自增**（不指定 → 閃掉 MariaDB 零陷阱）；程式碼**不寫死 id**，靠 env username 查列。

> **密碼契約：env 明文（`initial_admin_password`）而非 SSM hash（`initial_admin_password_hash`）**。啟動時 `hash_password(明文)` **一定產生合法 argon2 hash** → 「SSM 存非法 hash → root 登不進」的失效模式從源頭消失，且運維設定更直覺（毋須離線產 hash）。**取捨（誠實揭露）**：明文放 env/SSM 比 hash 更不設防——secret store 若被讀取，攻擊者**直接拿到密碼**（+ 重用風險），而非一個要爆破的 hash。緩解：①放 **SSM**（非 commit 的明文 env）；②config 用 `SecretStr`、**絕不 log**、不進錯誤訊息；③文件註明「bootstrap 憑證，首次登入後用 API 改掉」。此為業界常見模式（Grafana/Keycloak/Airflow 皆用明文 `ADMIN_PASSWORD` env）。

---

## 2. 目標設計（方案 X）

### 2.1 root 列（startup upsert 內容）
| 欄 | 值 | 來源 |
|---|---|---|
| `principals.role` | `1` | 常數 |
| `principals.id` / `admins.id` | **自增**（不指定） | DB |
| `admins.principal_id` | = 上面 principals 自增 id | 交易內捕捉 |
| `admins.username` | `settings.initial_admin_username` | **env** |
| `admins.name` | `settings.initial_admin_name or username` | **env** |
| `admins.admin_role` | `super_admin` | 常數 |
| `admins.is_protected` | `True` | 常數 |
| `admins.password_hash` | `await hash_password(settings.initial_admin_password)` | **env 明文 → 啟動 hash** |

- `is_protected=True` → 既有 `_guard_transition` + CHECK（`is_protected⟹super_admin`、`⟹active`）**擋掉被 archive/delete/降級**。
- **啟用條件**：`initial_admin_username` 與 `initial_admin_password` 皆非空才 seed（否則功能關閉、不建列）。

### 2.2 登入（`auth.py:admin_login`）— **完全一般化**
- **移除** `if is_initial_admin_username(...)` 整條 SSM 驗證分支。
- root 走**與所有 admin 相同**的路徑：`get_by_username` → `verify_password_or_dummy(admin.password_hash, pw)`（DB hash＝seed 時灌入）→ 發真實 `principal_id` 的 access + **refresh** token（真列後 family 綁得住，rotation 正常）。
- **零特判**；且 root 登入改用標準 constant-time 路徑 → 時序與其他 admin 無法區分（比哨兵分支更一致）。

### 2.3 改密碼（`admin.py:change_password`）— **開放**
- **移除** `admin.py:181` 的 `if admin_id == INITIAL_ADMIN_PRINCIPAL_ID → ForbiddenError("managed out-of-band")`。
- root 為真實 DB admin，`change_password` 走一般路徑（驗舊 DB hash → 換新 → 撤 token）。**是刻意政策反轉**：root 密碼從「SSM 管理、API 不可改」改為「進 DB、API 可改」。
- `is_protected` **不擋** change_password（只擋 archive/delete/降級），故 root 可改自己密碼。

### 2.4 移除的哨兵機制
| 檔案:位置 | 移除內容 |
|---|---|
| `initial_admin.py:20` | `INITIAL_ADMIN_PRINCIPAL_ID` 常數 |
| `initial_admin.py:44` | `build_initial_admin()`（synthetic Admin） |
| `initial_admin.py` | `initial_admin_hash()`（不再於登入用；密碼改 env 明文啟動 hash） |
| `auth.py:212-226` | `admin_login` 的 SSM 登入分支（改一般流程，§2.2） |
| `auth.py:408-412` | `get_admin_from_token` 的 `sub==0 → 合成 Admin` 分支 |
| `admin.py:181` | change_password 的初始 admin 403 守衛（§2.3） |
| `ws/router.py:83`、`ws/reauth.py:38` | `principal_id == INITIAL_ADMIN_PRINCIPAL_ID` 特判 |
| `admin.py:124-131` create | `is_initial_admin_username` 查重 → 改由 `admins.username` UNIQUE 兜底（保留亦可，冗餘無害） |

- **新增** `ensure_initial_admin(session)`（`initial_admin.py`）：startup upsert，§3。
- **config 改名**：`initial_admin_password_hash`（SSM hash）→ `initial_admin_password`（env 明文，`SecretStr`）。保留 `initial_admin_username` / `initial_admin_name`。

### 2.5 records 規格回改（已一併完成）
- root 為真實 DB admin → `records.created_by_principal_id` FK→`admins.principal_id` **自然滿足**。
- 已移除 `records-model.md` §2.1 bootstrap 守衛／§7.4／§8／§9-1a；`records-service.md` `_guard_not_initial_admin`／相關步驟／§6.4；`records-api.md` §0/§2/§6/§8.6 對應項。

---

## 3. 啟動時 upsert（`ensure_initial_admin`）

於 lifespan（`app.py`）startup 呼叫（yield 前）。

```
async def ensure_initial_admin(session):
    if 未啟用（username 或 password 空）: return              # 功能關閉，不 seed
    if await admin_repo.protected_root_exists(): return         # 冪等鍵：已有任何 root 就跳過（§3.1）
    u = settings.initial_admin_username
    pw_hash = await hash_password(settings.initial_admin_password.get_secret_value())  # 啟動 hash（to_thread）
    try:
        principal = await principal_repo.create(Role.ADMIN)     # id 自增；與下 add 同一交易
        session.add(Admin(principal_id=principal.id, username=u,
            name=settings.initial_admin_name or u,
            admin_role=super_admin, is_protected=True, password_hash=pw_hash))
        await session.commit()                                   # principal + admin 原子落地
    except IntegrityError:                                       # 併發輸家：另一 worker 已建
        await session.rollback()                                 # 視為已存在，no-op
```

### 3.1 冪等鍵：「有無 protected root」而非 username（🔴 保證最多一個 root）
- **冪等鍵 = `admin_repo.protected_root_exists()`**（查 `is_protected=True` 是否存在），**不用 `get_by_username`**。因 `is_protected` **只由本函式設**（一般 create API 恆 `is_protected=False`）→「`is_protected=True` 的列」⟺「bootstrap root」。
- **保證**：DB **只要已有一個 root 就不建新的**——不論 env username 是否改過。杜絕「env 改名 → 查不到同名 → 又長一個 → 兩個 live protected root」。
- **併發**：多 worker 於全新 DB 同時見「無 root」→ 都嘗試建（同一 env username）→ `admins.username` UNIQUE 讓輸家撞 `IntegrityError`；`principal + admin 同一交易` → 輸家整交易 rollback（**principal 不留孤兒**）→ catch → no-op。故不會 crash、也不會產生兩個 root。
- **rename 的取捨（見 §4.5）**：env username 改名後**不會**自動生效（已有 root 就跳過）；改 root 帳號變成**明確的手動操作**。這是刻意換取「單一 root 不變式」。

### 3.2 seed-once 語意
- 只在**無任何 root 時**建立（§3.1）；建立後**不再同步**。root 之後可用改密碼 API 自行換（DB 為真相），env 明文變 inert。**最像普通 admin**。
- ⚠️ **env 是 latent 憑證**：若 root **被刪到一個都不剩** / DB 重置，下次開機**用 env 明文重新 seed** → root 密碼退回 env 值。故 env 值須持續保密；改密碼後若要一致，另同步 SSM。

### 3.3 session 生命週期
- 用 `AsyncSessionLocal`（`app.py` 已 import）開一次性 session，commit 後關閉，勿洩漏。

### 3.4 毋須啟動驗 hash 格式
- 因啟動時**自產** hash（`hash_password`），必為合法 argon2 → **不需**「驗 SSM hash 格式」那步（那是 Y/存 hash 契約才需要的）。

---

## 4. 副作用評估（已據 lifespan/測試/change_password 查證）

### 4.1 ✅ 對既有測試零污染（關鍵）
- `tests/conftest.py:169` 明載「**ASGITransport 不跑 lifespan**」→ startup upsert 在測試 client 不觸發 → 不多種 super_admin → **principal/admin 計數斷言全不動、既有測試零改**。
- 初始 admin 相關測試改為**直接呼叫 `ensure_initial_admin`**（opt-in fixture）。
- ⚠️ 補一條 lifespan 接線測試（`asgi-lifespan`/`LifespanManager`）驗「lifespan 有呼叫 ensure_initial_admin」。

### 4.2 🟠 startup 新增硬性 DB 依賴
- 目前 lifespan startup 不做阻塞式 DB 寫入；加 upsert 後 DB 未起 / migration 未跑 → **app 開不了機**。對本 app 合理，但 **seed 失敗要 log 清楚訊息**（指出是初始 admin seed 階段）。

### 4.3 🟠 env 明文憑證：曝露取捨 + re-seed 陷阱
- **曝露**：env/SSM 存的是**明文密碼**（非 hash）；secret store 被讀 → 直接拿到密碼（+ 重用風險）。緩解見 §1（SSM + `SecretStr` + 不 log + 首登即改）。
- **re-seed 陷阱**（§3.2）：改密碼後 env 仍是舊明文；root 若被刪/DB 重置 → 開機用 env 重新 seed → 密碼退回舊值。env 視為 latent 憑證。

### 4.4 🟠 遺忘改後密碼 = DB 層救援
- root 改完密碼又忘 → env 救不了（root 還在 → 不 re-seed；`is_protected` 擋 API 刪除）。**復原路徑**：直接改 DB `password_hash`，或直接刪 root 列觸發 re-seed。**須寫入運維文件**。

### 4.5 🟡 其他（可接受）
- **改密碼撤 token**（既有）：`change_password` 撤全部 refresh token → root 改完用新密碼重登。
- **弱 bootstrap 密碼 × 無 rate limit**：env 密碼在被改前 live；若弱又不改，搭配「登入無 rate limit」（審查 H）可暴破 → bootstrap 密碼即使暫時也要夠強（rate limit 屬另一獨立修復）。
- **切換時舊哨兵 token 失效**：`sub=0` 舊 token → 查無 principal_id=0 admin → 401，需重登（一次性）。
- ✅ **env username 改名 → 不再產生重複 root**（已由 §3.1 的 is_protected 冪等鍵消除）：已有 root 就跳過，改名不自動生效、也不會多長一個。**取捨**：改 root 帳號需明確手動操作（改 DB，或刪掉唯一 root 再靠 re-seed），非「改 env 自動生效」。
- **`GET /admin/admins` 會列出 root**：是否預設濾 `is_protected` 屬 admin-management-api 決策（§6）。

### 4.6 ✅ 反而變好
- **防鎖死地板**：protected root 恆在 → 系統永遠 ≥1 super_admin，杜絕「最後兩個 super_admin 互刪到歸零 / 鎖死」（現有守衛皆單列、無計數守衛，靠此地板兜底）。
- **records 解鎖**：`created_by` FK 對 root 滿足 → records bootstrap 守衛移除。

### 4.7 可選硬化（與 root 無關的一般衛生）
- `verify_password` 多 catch `InvalidHashError → return False` + WARNING log：萬一 DB 有壞 hash（舊資料/手動改壞），登入回 401（統一訊息、防列舉）而非 500，並留運維線索。與本重構正交、可獨立先做。

---

## 5. TDD 測試計畫（先寫、先看 RED）

### 5.1 新 RED
- **`ensure_initial_admin` 單元**：未設 env → no-op（無列）；設 env → 建一筆 `super_admin`/`is_protected`，`password_hash` 可用 env 明文 `verify` 通過；**再呼叫一次 → 仍只有一筆**（seed-once 冪等）。
- **單一 root 不變式（§3.1）**：DB 已有一個 protected root 時呼叫 `ensure_initial_admin`（**即使 env username 不同**）→ **不建第二個**，protected root 數恆為 1。`protected_root_exists()` 命中/未命中各驗一次。
- **C2 迴歸（核心）**：seed root → root 登入取 token → `DELETE /admin/admins/{id}`／`archive` 另一 admin → **成功，`deleted_by/archived_by` = root 的 principal_id**（非 NULL、非 500）。哨兵版此測試現在跑會 500。
- **登入一般化**：root 用 env 密碼登入 → 200 + 發 refresh token；錯密碼 → 401。
- **改密碼開放**：root 改自己密碼 → 200、撤 token；用舊密碼再登 → 401、新密碼 → 200。
- **root 受保護**：對 root `archive`/`delete`/降級 → 422（`is_protected`）。
- **lifespan 接線**：`LifespanManager` 起 app → root 列存在。

### 5.2 既有測試調整
- `tests/conftest.py`：opt-in fixture（設 env + 呼叫 `ensure_initial_admin`）供初始 admin 測試；**預設不啟用**。
- 既有 `test_initial_admin.py`、`test_admin_auth_api.py`：改「真列」語意（DB 查得到、發 refresh token、可改密碼、無 synthetic）。

---

## 6. Open Questions
1. ~~X vs Y~~ → **X 定案**（§1）。
2. ~~密碼契約~~ → **env 明文 `initial_admin_password` 定案**（§1）；曝露取捨已接受（SSM + SecretStr + 首登即改）。
3. ~~seed-once vs sync-always~~ → **seed-once 定案**（§3.2）。
4. **`GET /admin/admins` 是否濾 `is_protected`**：屬 admin-management-api 決策。
5. ~~env username 改名的孤兒列~~ → **已由 §3.1 的 is_protected 冪等鍵消除**（已有 root 就不建）。改 root 帳號改為明確手動流程；是否要提供「retire/rename root」的管理端點屬後續。
6. **既有部署**：已用哨兵運作過的 DB 是否有 `principal_id=0` 稽核殘留？MariaDB FK 強制下哨兵 archive/delete 本會 500（無成功殘留）；動手前確認一次。

---

## 7. 影響檔案清單（實作時逐一 TDD）
- `app/core/config`（`initial_admin_password_hash` → `initial_admin_password`，`SecretStr`；啟用條件）
- `app/services/initial_admin.py`（移除哨兵常數/synthetic/`initial_admin_hash`；新增 `ensure_initial_admin`；§2.4）
- `app/repositories/admin.py`（新增 `protected_root_exists() -> bool`：`SELECT 1 ... WHERE is_protected = true LIMIT 1`，供 §3.1 冪等鍵）
- `app/app.py`（lifespan startup 呼叫 `ensure_initial_admin`）
- `app/services/auth.py`（登入一般化、移除 `sub==0` 合成分支；§2.2）
- `app/services/admin.py`（移除 change_password 的初始 admin 403、`== INITIAL_ADMIN_PRINCIPAL_ID` 特判）
- `app/api/routers/ws/router.py`、`app/services/ws/reauth.py`（移除哨兵特判）
- `tests/conftest.py`（opt-in seed fixture）＋ 初始 admin 專屬測試改真列語意
- （可選）`app/core/auth/password.py`（`InvalidHashError` 硬化，§4.7）
- 記憶 `initial-admin-sentinel-fk-gotcha`（標記已根治）
- **無 alembic revision**（schema 無變更；身分為 env 驅動故走 startup upsert）
- 運維文件：bootstrap 密碼為 latent 憑證、首登即改、遺忘密碼的 DB 救援路徑（§4.3/§4.4）
