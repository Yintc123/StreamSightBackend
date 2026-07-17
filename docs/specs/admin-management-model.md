# 規格書（Model 層）：Admin 管理 — 資料模型

> 狀態：**Draft（待實作）** ／ 目標版本：next+2 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「Admin 管理」功能三份規格的 **Model 層**（資料模型）。另兩份：
> - [`admin-management-service.md`](./admin-management-service.md)（業務邏輯層）
> - [`admin-management-api.md`](./admin-management-api.md)（HTTP API 層）
>
> 🔗 本組規格共同**交付** [`admin-account-refinement.md`](./admin-account-refinement.md) §1／§11.1 與 [`rbac.md`](./rbac.md) §11.3 延遲的「**Admin 管理 API**」（建立／更新／封存／刪除／升降權其他 admin 的 CMS 端點）。
>
> ⚠️ **分工要點**：`Admin` 的資料模型（username 登入、`admin_role` 等級欄、`archived_*`／`deleted_*` 封存與軟刪除、`is_active` 計算屬性）**已由 [`admin-account-refinement.md`](./admin-account-refinement.md) §3 完整交付並落地**（migration `c3d4e5f6a7b8`，已於真 MariaDB 驗證）。本文的核心結論是：**Admin 管理功能無需新增任何資料表或欄位、無需新 migration**——只是把既有 schema 收斂為「管理」視角的**權威資料模型參考**，並明確標注哪些是已交付、哪些是本組新增的**讀取／查詢**需求（repository 層），不動 DDL。

---

## 1. 背景與目標

「Admin 管理」要支援對 **其他 admin 帳號** 的完整生命週期操作：**登入**（既有）、**新增**、**更新**（顯示名稱、密碼、權限等級）、**封存／解除封存**、**軟刪除／復原**、**列表查詢**。這些操作的 service／API 設計見另兩份規格；本文只回答 **「資料模型是否足以支撐？需要改什麼？」**

### 目標

- 給出 Admin 管理功能的**權威資料模型參考**（欄位、約束、狀態機、不變式），作為 service／API 規格的共同地基。
- **明確界定**：本功能對 schema 的增量 = **零**（不加表、不加欄、不加 migration）。逐項說明每個管理操作各自落在哪些既有欄位。
- 定義 **repository 層的新增查詢方法**（列表用「時間戳謂詞」查詢），這屬**讀取程式碼**、非 schema 變更。
- 收斂三個易混淆點：`role` vs `admin_role`、`is_active` 計算屬性不可進 SQL、稽核成對不變式。

### 非目標（Out of scope）

- **service／業務規則**（安全不變式、交易邊界、token 撤銷）→ [`admin-management-service.md`](./admin-management-service.md)。
- **HTTP 端點／DTO／授權**→ [`admin-management-api.md`](./admin-management-api.md)。
- **授權機制本身**（`grade` claim、`require_min_admin_role`、`ADMIN_ROLE_RANK`）→ [`rbac.md`](./rbac.md)。
- **purge（永久清除）**：本專案不採（承 [`admin-account-refinement.md`](./admin-account-refinement.md) §2.6），故無物理刪除路徑。
- **User（role=0）**：完全不動。

---

## 2. 設計決策

### 2.1 管理功能對 schema 的增量＝零（不新增表／欄／migration）

逐一檢視每個管理操作寫入哪裡，證明既有 `admins` 欄位已足夠：

| 管理操作 | 寫入的既有欄位 | 是否需新欄位 |
|---|---|---|
| 登入（既有） | 讀 `username` / `password_hash`；讀 `is_active`（計算屬性） | 否 |
| 新增 | `username` / `name` / `password_hash` / `admin_role`（+ `principals` 一列） | 否 |
| 更新顯示名稱 | `name`（`updated_at` 由 ORM `onupdate` 自動） | 否 |
| 更新密碼 | `password_hash` | 否 |
| 升降權（set_role） | `admin_role` | 否 |
| 封存 / 解除封存 | `archived_at` / `archived_by`（成對） | 否 |
| 軟刪除 / 復原 | `deleted_at` / `deleted_by`（成對） | 否 |
| 列表 / 明細查詢 | 讀既有欄位（`archived_at` / `deleted_at` 作謂詞） | 否 |

> **結論**：**無新 migration**。目前 alembic head = `c3d4e5f6a7b8`（已含全部所需欄位）。本組規格的實作里程碑（見 service／api 規格）**不含任何 alembic revision**。

### 2.2 不新增 `updated_by`（一般更新的稽核走結構化 log，非欄位）

封存／軟刪除是**高稽核的狀態轉移**，故各配一稽核操作者欄（`archived_by` / `deleted_by`，已交付）。但「改名／改密碼／升降權」若也各加 `*_by` 欄會使 `admins` 迅速膨脹且語意重疊。**定案**：

- **一般更新不加 `updated_by` 欄**。`updated_at`（既有，`onupdate=func.now()`）記「何時最後被改」；**「誰改了什麼」以結構化 log 記錄**（service 層 `logger.info("admin update ...", actor=..., target=..., field=...)`）。
- **升降權（set_role）** 是敏感操作，**至少**要有 log 稽核（誰把誰從 X 改到 Y）；是否要專用 `admin_role_change_events` 稽核表列為 **Open Question**（§7），本組先以 log 交付、schema 不變。
- 此決策讓 `admins` 只保留「狀態轉移（archive/delete）」兩對稽核欄，避免每種可變欄都拖一個 `*_by`。

### 2.3 `username` 建立後不可變（無 rename 欄位語意）

`username` 是登入識別＋唯一索引＋（未來）稽核關聯鍵。**管理功能不提供改 username**：

- 避免「改名後舊名可被他人佔用」的唯一性／稽核複雜度（與 [`admin-account-refinement.md`](./admin-account-refinement.md) §2.6「username 永久保留」精神一致）。
- 需要「換人」時的正解是**軟刪除舊帳號 + 新建新帳號**，而非原地改名。
- 故資料模型上 `username` 視同**建立後 immutable**；`update` 路徑（service／api）**不接受** `username` 欄。若未來確有改名需求，另立規格並處理唯一性與稽核遷移。

### 2.4 兩個「role」欄的正交軸（實作務必辨明，勿混用）

`admins` 有兩個 role 相關欄，**用途完全不同**（[`admin-account-refinement.md`](./admin-account-refinement.md) §2.7、[`rbac.md`](./rbac.md) §3.2 已詳述，此處為管理功能再次強調）：

| 欄位 | 型別 | 語意 | 可變性 | 管理操作 |
|---|---|---|---|---|
| `role` | `SmallInteger`（常數 `1`） | **型別判別子**（「這是 admin」），被複合 FK + `CHECK(role=1)` 釘死 | **不可變** | 任何管理操作**都不碰** |
| `admin_role` | `String(20)`（`super_admin`/`editor`/`viewer`） | **權限等級**，供授權與 `grade` claim | 可變 | 由 **set_role（升降權）** 修改 |

> ⚠️ 「升降權」改的是 **`admin_role`**，**永遠不改** `role`。DTO 對外只出現 `admin_role`，`role` 不進 DTO。

### 2.5 列表查詢用時間戳謂詞，`is_active` 不可進 SQL WHERE

`is_active` 是 **Python 計算屬性**（`archived_at IS NULL AND deleted_at IS NULL`），**不可**用於 SQL `WHERE`（承 [`admin-account-refinement.md`](./admin-account-refinement.md) §5.2 的⚠️）。管理**列表**需要「篩 active／已封存／已刪」時，一律用**欄位謂詞**：

| 狀態 | SQL 謂詞 |
|---|---|
| active | `archived_at IS NULL AND deleted_at IS NULL` |
| archived（僅封存、未刪） | `archived_at IS NOT NULL AND deleted_at IS NULL` |
| deleted（軟刪除，終態） | `deleted_at IS NOT NULL` |

故 repository 需**新增以欄位查詢的方法**（見 §4），這是讀取程式碼、非 schema 變更。

---

## 3. 資料模型（權威參考，已交付；此處為收斂）

> 以下為 `app/models/admin.py` 現況（[`admin-account-refinement.md`](./admin-account-refinement.md) §3.1 交付）。**本組規格不修改此檔**，列此作為 service／api 規格的欄位契約基準。

### 3.1 欄位

| 欄位 | 型別 | 約束 / 預設 | 說明 |
|---|---|---|---|
| `id` | `int` PK | Base 提供 | |
| `principal_id` | `int` | `unique`, `index`；複合 FK → `principals` | 一對一掛 principal |
| `role` | `SmallInteger` | 常數 `1`；`CHECK(role=1)`；複合 FK 一員 | 型別判別子（不可變） |
| `username` | `String(100)` | `unique`, `index`；非加密 | 登入識別、正規化小寫（建立後不可變，§2.3） |
| `name` | `String(100)` | | 顯示名稱（可更新） |
| `password_hash` | `String(255)` | | argon2id（可更新） |
| `admin_role` | `String(20)` | `default`/`server_default`=`viewer`；`CHECK IN (...)` | 權限等級（可由 set_role 更新） |
| `archived_at` | `DateTime(tz)` nullable | 預設 `None` | 封存時間（成對稽核） |
| `archived_by` | `int` nullable | FK → `principals.id`, `ON DELETE SET NULL` | 封存操作者 |
| `deleted_at` | `DateTime(tz)` nullable | 預設 `None` | 軟刪除時間（成對稽核） |
| `deleted_by` | `int` nullable | FK → `principals.id`, `ON DELETE SET NULL` | 軟刪除操作者 |
| `created_at` / `updated_at` | `DateTime(tz)` | Base 提供（`onupdate`） | |

### 3.2 約束（`__table_args__`）

- `ForeignKeyConstraint(["principal_id","role"] → ["principals.id","principals.role"], ON DELETE CASCADE, name="fk_admins_principal_role")`
- `CheckConstraint("role = 1", name="ck_admins_role_admin")`
- `CheckConstraint("admin_role IN ('super_admin','editor','viewer')", name="ck_admins_admin_role")`
- 唯一索引：`ix_admins_principal_id`、`ix_admins_username`

### 3.3 計算屬性

```python
@property
def is_active(self) -> bool:
    """封存或軟刪除皆視為不可用（登入／refresh／授權共用）。不可進 SQL WHERE。"""
    return self.archived_at is None and self.deleted_at is None
```

### 3.4 狀態機（資料層）

| 狀態 | 條件 | is_active | 可轉移到 |
|---|---|---|---|
| **active** | `archived_at IS NULL AND deleted_at IS NULL` | ✅ | archived（archive）、deleted（delete） |
| **archived** | `archived_at IS NOT NULL AND deleted_at IS NULL` | ❌ | active（unarchive）、deleted（delete） |
| **deleted** | `deleted_at IS NOT NULL`（終態，優先於 archived） | ❌ | active（restore） |

> 業務層的轉移方法與其守衛規則見 [`admin-management-service.md`](./admin-management-service.md) §3。

### 3.5 資料不變式（管理操作必須維持）

1. **成對稽核不變式**：`archived_at IS NULL ⟺ archived_by IS NULL`；`deleted_at IS NULL ⟺ deleted_by IS NULL`。任何轉移後都成立（set 時同寫、clear 時同清）。
2. **`role` 恆為 1**：管理操作不得改動（型別判別子）。
3. **`username` 唯一且不可變**：軟刪除者仍佔用其 username（不 purge → 永久保留）。
4. **`admin_role` 值域**：恆為 `{super_admin, editor, viewer}` 之一（`CHECK` 硬化）。

---

## 4. Repository 層增量（讀取程式碼，非 schema）

> 承 [`admin-account-refinement.md`](./admin-account-refinement.md) §5.2：repository 維持「dumb」，回列即可；可用性由 service 讀 `is_active` 判定。**列表**需以欄位謂詞查詢，故新增下列方法於 `app/repositories/admin.py`（**無 DDL**）。

- `list_admins(*, status: AdminStatusFilter, limit: int, offset: int) -> Sequence[Admin]`
  依 `status` 套用 §2.5 的時間戳謂詞：
  - `ACTIVE` → `archived_at IS NULL AND deleted_at IS NULL`
  - `ARCHIVED` → `archived_at IS NOT NULL AND deleted_at IS NULL`
  - `DELETED` → `deleted_at IS NOT NULL`
  - `ALL` → 無狀態謂詞（含全部）
  排序建議 `ORDER BY id`（穩定分頁）。
- `count_admins(*, status: AdminStatusFilter) -> int`：同上謂詞的計數（供列表分頁 total）。
- `count_active_super_admins() -> int`：`admin_role = 'super_admin' AND archived_at IS NULL AND deleted_at IS NULL` 的計數——供 service 的「最後一位 super_admin」安全不變式（見 [`admin-management-service.md`](./admin-management-service.md) §3.5）。

> `AdminStatusFilter` 是本組新增的小型 `StrEnum`（`active`/`archived`/`deleted`/`all`），放 `app/core/enums.py`，供 repository／service／api 共用（見 service 規格 §2）。

---

## 5. 安全性考量（資料層）

- **登入識別非 PII**：`username`／`name` 明文非個資；`admins` 已無 `email`（PII）。管理列表回傳這些欄位無 PII 外洩顧慮。
- **稽核完整性**：`archived_by`／`deleted_by` 為 `SET NULL`——即使未來操作者 principal 被移除，被稽核列不受影響（不連坐、不抹除歷史）。
- **值域硬化**：`admin_role` 的 `CHECK` 由 DB 強制，管理端即使繞過 service 也無法寫入非法等級（integrity-first）。
- **`is_active` 計算屬性防呆**：管理列表若誤用 `is_active` 下 SQL filter 會在 SQLite 綠、MariaDB 行為不一致——§2.5 已強制改用時間戳謂詞。

---

## 6. TDD 測試計畫（資料層；先寫、先看到 RED）

> 模型本身多數已有測試（[`admin-account-refinement.md`](./admin-account-refinement.md) §8.1 已覆蓋 `is_active` 計算、`admin_role` 預設／CHECK、username 唯一）。本組**新增 repository 查詢**的測試：

### 6.1 Unit — Repository（`tests/unit/repositories/test_admin.py` 增補）
- `list_admins(status=ACTIVE)`：只回 `archived_at`／`deleted_at` 皆 NULL 者；封存／軟刪除者不出現。
- `list_admins(status=ARCHIVED)`：只回「已封存且未刪」者。
- `list_admins(status=DELETED)`：只回軟刪除者。
- `list_admins(status=ALL)`：三種狀態全回。
- `list_admins` 分頁：`limit`/`offset` 正確切片、`ORDER BY id` 穩定。
- `count_admins(status=...)`：與對應 `list` 筆數一致。
- `count_active_super_admins()`：只計 `admin_role='super_admin'` 且 active 者；封存／軟刪除的 super_admin 不計入；editor/viewer 不計入。

### 6.2 Unit — enum
- `AdminStatusFilter` 四個成員字串值（`active`/`archived`/`deleted`/`all`）。

---

## 7. 已定案決策

- ✅ **Admin 管理功能對 schema 增量＝零**：不新增表／欄／migration；每個操作皆落在既有欄位（§2.1）。
- ✅ **不加 `updated_by` 欄**：一般更新／升降權的稽核走結構化 log；狀態轉移仍用既有 `archived_by`／`deleted_by`（§2.2）。
- ✅ **`username` 建立後不可變**：管理更新不接受 username；換人＝軟刪除 + 新建（§2.3）。
- ✅ **`role` vs `admin_role` 正交**：升降權只改 `admin_role`，`role` 永不動（§2.4）。
- ✅ **列表用時間戳謂詞**，`is_active` 不進 SQL；新增 repository `list_admins`／`count_admins`／`count_active_super_admins`（§2.5、§4）。
- ✅ 新增 `AdminStatusFilter` enum（`active`/`archived`/`deleted`/`all`），跨層共用（§4）。

## 8. 待確認事項（Open Questions）

1. **升降權稽核是否需專用 audit 表**（`admin_role_change_events`：who/target/from/to/at）？本組先以 log 交付、schema 不變（§2.2）；若合規或營運需查詢歷史，再另立規格加表。
2. **列表分頁上限**與預設 `limit`（建議 default 50、max 200）——由 api 規格定案（§4 只定方法簽名）。
