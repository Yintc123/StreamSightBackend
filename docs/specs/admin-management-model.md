# 規格書（Model 層）：Admin 管理 — 資料模型

> 🔄 **變更註記（初始 admin 整併）**：本文已改寫為「**≥1 super_admin 由 SSM 初始 admin 保證、`is_protected` 為可選休眠硬化**」的模型（§2.1／§2.2）;seed 腳本（`scripts/create_admin.py`）已移除,bootstrap 改走 SSM 初始 admin（見 [`admin-management-service.md`](./admin-management-service.md) §3.7、`app/services/initial_admin.py`）。

> 狀態：**已實作（✅ 547 tests 全綠，ruff / pyright 通過）** ／ 目標版本：next+2 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「Admin 管理」三份規格的 **Model 層**（資料模型）。另兩份：
> - [`admin-management-service.md`](./admin-management-service.md)（業務邏輯層）
> - [`admin-management-api.md`](./admin-management-api.md)（HTTP API 層）
>
> 🔗 本組規格共同**交付** [`admin-account-refinement.md`](./admin-account-refinement.md) §1／§11.1 與 [`rbac.md`](./rbac.md) §11.3 延遲的「**Admin 管理 API**」。**前置依賴**：本組（next+2）依賴 [`rbac.md`](./rbac.md)（next+1）的 `require_min_admin_role` 與 `grade` claim——**rbac 必須先落地**。
>
> ⚠️ **與 [`admin-account-refinement.md`](./admin-account-refinement.md) 的分工**：`Admin` 既有欄位（username 登入、`admin_role` 等級、`archived_*`／`deleted_*`、`is_active` 計算屬性）已由其 §3 交付。本組**只新增一欄 `is_protected`**（受保護 root 標記）並新增一支 append-only migration——理由見 §2.1。

---

## 0. 功能總覽（三份規格入口，先讀這裡）

**一句話**：CMS 對「其他 admin 帳號」的完整生命週期管理——登入（既有）＋新增／更新名稱／升降權／改自己密碼／封存／解除封存／軟刪除／復原／列表；並以 **SSM 初始 admin** 保證系統永遠 ≥1 位可用的 super_admin（DB 內另備一套**可選、休眠**的單列「受保護 root」硬化）。

**三份規格分層（閱讀順序：本檔 → service → api）**：

| 層 | 檔案 | 內容 |
|---|---|---|
| **Model**（本檔，入口） | `admin-management-model.md` | 欄位／約束／狀態機／不變式、`is_protected`、migration、repository 查詢 |
| **Service** | [`admin-management-service.md`](./admin-management-service.md) | `update`／`change_password`／`set_admin_role`／`list_admins` + 單列守衛 |
| **API** | [`admin-management-api.md`](./admin-management-api.md) | HTTP 端點／DTO／授權／狀態碼 |

**操作 → 落點速查**：

| 操作 | Service | Endpoint |
|---|---|---|
| 登入（既有） | `admin_login` | `POST /admin/auth/login` |
| 列表／明細 | `list_admins`／`get_row` | `GET /admin/admins`／`GET /admin/admins/{id}` |
| 新增 | `create` | `POST /admin/admins` |
| 更新名稱 | `update` | `PATCH /admin/admins/{id}` |
| 升降權 | `set_admin_role` | `PUT /admin/admins/{id}/role` |
| 改自己密碼 | `change_password` | `POST /admin/me/password` |
| 封存／解除封存 | `archive`／`unarchive` | `POST /admin/admins/{id}/archive`｜`/unarchive` |
| 軟刪除／復原 | `delete`／`restore` | `DELETE /admin/admins/{id}`｜`POST /{id}/restore` |

> **不提供**：重設他人密碼、改 username、purge、transfer-ownership、切換 `is_protected`（理由散見各 §）。
>
> **前置依賴**：本功能（next+2）依賴 [`rbac.md`](./rbac.md)（next+1）的 `require_min_admin_role` 與 `grade`——**rbac 必須先落地**。
>
> **核心決策**：§2.1「≥1 super_admin」由 **SSM 初始 admin** 保證（不碰 DB、無聚合、無鎖、無 write skew）；`is_protected` 為 DB 內**可選、休眠**的單列硬化。

---

## 1. 背景與目標

「Admin 管理」要支援對**其他 admin 帳號**的完整生命週期：**登入**（既有）、**新增**、**更新**（顯示名稱、密碼、權限等級）、**封存／解除封存**、**軟刪除／復原**、**列表查詢**，並維持一條關鍵不變式：

> **系統永遠至少保有 1 位可用的 SUPER_ADMIN**（否則無人能管理 admin，系統鎖死）。

本文回答資料模型層面：**如何用最單純、併發安全的方式支撐上述操作與該不變式。**

### 目標

- 給出 Admin 管理功能的**權威資料模型參考**（欄位、約束、狀態機、不變式）。
- **新增 `admins.is_protected`**（可選的單列「受保護 root」硬化）+ 一支 append-only migration + 兩條 `CHECK`。日常「≥1 super_admin」主保證為 **SSM 初始 admin**（§2.1）；`is_protected` 目前休眠、保留作可選 DB 硬化。
- 定義 repository 的**列表查詢方法**（時間戳謂詞），屬讀取程式碼。
- 收斂三個易混淆點：`role` vs `admin_role`、`is_active` 不可進 SQL、成對稽核不變式。

### 非目標（Out of scope）

- **service／業務規則**（守衛、交易邊界、token 撤銷）→ [`admin-management-service.md`](./admin-management-service.md)。
- **HTTP／DTO／授權**→ [`admin-management-api.md`](./admin-management-api.md)。
- **授權機制**（`grade`、`require_min_admin_role`、`ADMIN_ROLE_RANK`）→ [`rbac.md`](./rbac.md)。
- **purge**：不採（承 admin-account-refinement §2.6）。
- **User（role=0）**：完全不動。

---

## 2. 設計決策

### 2.1 「≥1 super_admin」的保證：SSM 初始 admin（主）＋ 可選的單列受保護 root（休眠）

「系統至少 1 位可用的 super_admin」若以「對 super_admin 集合計數」實作，是一個**集合基數不變式**——併發下會發生 **write skew**（兩個交易各降級／移除不同的 super_admin，各自讀到 count≥2 而放行 → 集合歸零）。防護它需 `SELECT ... FOR UPDATE` 鎖住整個集合或 SERIALIZABLE，複雜且在 SQLite 測不到。**本設計刻意避開這條路**。

**主保證：SSM-backed「初始 admin」**（見 [`admin-management-service.md`](./admin-management-service.md) §3.7、`app/services/initial_admin.py`）。憑證存 config／SSM（`INITIAL_ADMIN_USERNAME` + `INITIAL_ADMIN_PASSWORD_HASH`）、**不進 `admins` 表**、以哨兵 `principal_id=0` 代表、恆為 `super_admin`、無法被降級／封存／刪除／改密碼／鎖死;只要 SSM 有其雜湊就永遠可登入。故「≥1 active super_admin」**恆成立,且完全不碰 DB——零計數、零鎖、零 write skew**。它比「DB 內的受保護 root」更強:連 DB 都無從影響它。這對應 GitHub org owner / AWS root 帳號的定位:一個**結構上不可移除的擁有者**,其餘（DB 內的）super_admin 可自由增刪。

**可選的 DB 層硬化：`admins.is_protected`（單列受保護 root，現為休眠）。** 保留 `is_protected` 欄 + service 單列守衛 + 兩條 CHECK（§2.3），作為「把某個 **DB** super_admin 標記為結構上不可移除」的**可選**機制。其設計沿用**單列不變式**（皆只讀 target 自己那一列、零聚合、零鎖、零 write skew——正是為避開上述集合鎖而設計）：

- 配合 service 單列守衛（見 service §3.5）：**受保護者不可被降級／封存／軟刪除**;且（工作流規則）**任何 super_admin 須先 `set_admin_role` 降級才能封存／刪除**。
- 受保護者恆為 active super_admin,除「誰把它建為 protected」外全由兩條 CHECK 結構保證（§2.3）。
- **但目前休眠**：seed 已移除、管理 API 恆建 `is_protected=False`、且不提供切換端點 → **預設無任何 DB 列被標 protected**。日常的「≥1 super_admin」由 SSM 初始 admin 承擔，不依賴它;若未來想在 DB 內也釘一個不可移除的 owner，才用此機制（例如以一次性 script 標記）。

> **代價（誠實揭露）**：`is_protected` 機制對 schema 的增量＝**+1 欄 + 2 條單列 CHECK + 1 支 migration**。即使目前休眠仍保留在 schema／code（migration 已部署、不可變），作為零聚合、零鎖的可選硬化。

### 2.2 `is_protected` 的性質：可選、單列、不可經 API 切換

- `is_protected` **無自動建立點**（seed 已移除）;**管理 API 一律建立 `is_protected=False`、且不提供切換端點**。若要標記，走 DB／一次性 script（out of scope 於管理 API）。
- **不提供「取消保護」切換**：若允許任意取消，就會冒出「不可取消最後一個受保護者」這種**新的聚合不變式** → write skew 回歸。故若使用 `is_protected`，一律**建立後不可變**。
- **允許多個受保護 root**：`is_protected` 無唯一約束、無「恰好一個」的基數約束（那又是聚合問題）。
- **繼任（transfer ownership）** 屬 out of scope。
- ⚠️ **不再有「seed 建 protected super_admin」這個系統前置條件**：`≥1 super_admin` 改由 SSM 初始 admin 保證（§2.1），不依賴任何 DB protected 列。

### 2.3 兩條單列 CHECK 硬化受保護 root（`protected ⟹ super_admin` ＋ `protected ⟹ active`）

**CHECK 一** `CheckConstraint("is_protected = 0 OR admin_role = 'super_admin'", name="ck_admins_protected_is_super")`：DB 層保證**受保護者必為 super_admin**（integrity-first，對齊既有 `ck_admins_admin_role` 風格）。搭配「受保護者不可降級」的 app 守衛（防呆友善訊息），形成雙保險——即使繞過 service，DB 也擋下「把 protected 改成非 super_admin」或「建立 protected 的非 super_admin」。此 CHECK 同時**間接硬化「受保護者不可降級」**：任何把 protected 降為 editor／viewer 的寫入都違反它。

**CHECK 二（A）** `CheckConstraint("is_protected = 0 OR (archived_at IS NULL AND deleted_at IS NULL)", name="ck_admins_protected_is_active")`：DB 層保證**受保護者恆為 active**（未封存、未軟刪除）。理由——`protected ⟹ super_admin`（CHECK 一）與「不可降級」已由 CHECK 一硬化，「受保護者不可封存／刪除」原本只靠 app 守衛;補這條後,`protected ⟹ super_admin` ＋ `protected ⟹ active` 使「受保護者恆為 active super_admin」**全由 DB 結構保證**（只剩「誰把它建為 protected」是非 DB 前提）。此 CHECK 只擋「封存／刪除受保護 root」這個設計上不該發生的操作，正常流程碰不到；service 的受保護守衛保留作友善訊息（422），DB CHECK 作結構兜底（`IntegrityError`）。

> 這兩條 CHECK 只在 `is_protected=True` 的列上有意義;目前預設無 protected 列（§2.1 休眠），故 CHECK 恆滿足（`is_protected=0` 短路）,屬「若未來使用 is_protected 即自動生效」的結構保險。

> **布林比較方言**：`is_protected = 0` 對齊既有 `ck_admins_role_admin`（`role = 1`）風格，於 MariaDB（BOOLEAN=TINYINT）／SQLite（0/1）皆正確（本專案已由 PostgreSQL 切換至 MariaDB）。

### 2.4 一般更新不新增 `updated_by`（走結構化 log）

承前設計：封存／軟刪除是高稽核狀態轉移，各配 `archived_by`／`deleted_by`（已交付）。改名／改密碼／升降權**不各加 `*_by` 欄**（避免 `admins` 膨脹）；`updated_at` 記「何時最後被改」，「誰改了什麼」以結構化 log 記。升降權稽核是否需專用 audit 表列 Open Question（§8）。

### 2.5 `username` 建立後不可變

`username` 是登入識別＋唯一索引。管理功能**不提供改 username**（避免改名後舊名被佔用的唯一性／稽核複雜度，承 admin-account-refinement §2.6 精神）；「換人」＝軟刪除舊帳號 + 新建。`update` 路徑不接受 `username`。

### 2.6 兩個「role」欄正交（勿混用）

`role`（`SmallInteger` 常數 1）＝型別判別子（不可變，任何管理操作都不碰）；`admin_role`（`String`）＝權限等級（由 **set_admin_role** 升降權修改）。升降權改的是 `admin_role`，**永不改** `role`。

### 2.7 列表用時間戳謂詞，`is_active` 不進 SQL

`is_active` 是 Python 計算屬性，不可用於 SQL `WHERE`（承 admin-account-refinement §5.2）。列表篩選一律用欄位謂詞：

| 狀態 | SQL 謂詞 |
|---|---|
| active | `archived_at IS NULL AND deleted_at IS NULL` |
| archived | `archived_at IS NOT NULL AND deleted_at IS NULL` |
| deleted | `deleted_at IS NOT NULL` |

---

## 3. 資料模型

### 3.1 欄位（既有 + 新增 `is_protected`）

| 欄位 | 型別 | 約束 / 預設 | 說明 |
|---|---|---|---|
| `id` | `int` PK | Base 提供 | |
| `principal_id` | `int` | `unique`, `index`；複合 FK | 一對一掛 principal |
| `role` | `SmallInteger` | 常數 `1`；`CHECK(role=1)` | 型別判別子（不可變） |
| `username` | `String(100)` | `unique`, `index` | 登入識別（建立後不可變，§2.5） |
| `name` | `String(100)` | | 顯示名稱（可更新） |
| `password_hash` | `String(255)` | | argon2id（可更新） |
| `admin_role` | `String(20)` | 預設 `viewer`；`CHECK IN (...)` | 權限等級（set_admin_role 更新） |
| **`is_protected`** | **`Boolean`** | **`default False` / `server_default false`（本組新增）** | **受保護 root 標記（可選、不可經 API 切換；預設無列被標，§2.2）** |
| `archived_at` / `archived_by` | `DateTime(tz)` / `int` nullable | `by` FK→principals `SET NULL` | 封存（成對稽核） |
| `deleted_at` / `deleted_by` | `DateTime(tz)` / `int` nullable | `by` FK→principals `SET NULL` | 軟刪除（成對稽核） |
| `created_at` / `updated_at` | `DateTime(tz)` | Base（`onupdate`） | |

### 3.2 約束（`__table_args__`）

既有：`fk_admins_principal_role`（複合 FK, CASCADE）、`ck_admins_role_admin`（role=1）、`ck_admins_admin_role`（admin_role 值域）、`ix_admins_principal_id`、`ix_admins_username`。

**新增**：
- `CheckConstraint("is_protected = 0 OR admin_role = 'super_admin'", name="ck_admins_protected_is_super")`（受保護者必為 super_admin，§2.3）。
- `CheckConstraint("is_protected = 0 OR (archived_at IS NULL AND deleted_at IS NULL)", name="ck_admins_protected_is_active")`（受保護者恆 active，§2.3，A）。

### 3.3 計算屬性（不變）

```python
@property
def is_active(self) -> bool:
    return self.archived_at is None and self.deleted_at is None
```

### 3.4 狀態機（資料層）

| 狀態 | 條件 | is_active |
|---|---|---|
| active | `archived_at IS NULL AND deleted_at IS NULL` | ✅ |
| archived | `archived_at IS NOT NULL AND deleted_at IS NULL` | ❌ |
| deleted | `deleted_at IS NOT NULL`（終態） | ❌ |

> 轉移的守衛規則（含 protected／super_admin／self）見 [`admin-management-service.md`](./admin-management-service.md) §3.5。

### 3.5 資料不變式

1. **成對稽核（寫入時不變式，E）**：狀態轉移時 `archived_at` 與 `archived_by` **同時寫入**（`deleted_*` 同理）。⚠️ **非永久 iff**——`archived_by`／`deleted_by` 為 `FK ON DELETE SET NULL`，若操作者 principal 日後被移除，`*_by` 會轉 `NULL` 而 `*_at` 仍在（稽核完整性取捨，§5）。實務上 admin 只軟刪、principal 從不硬刪，故 SET NULL 幾乎不觸發，但不變式仍以「寫入時成對」表述，而非宣稱永久 `⟺`。
2. **`role` 恆 1**（型別判別子）。
3. **`username` 唯一且不可變**；軟刪除者永久保留其 username（不 purge）。
4. **`admin_role` 值域** ∈ `{super_admin, editor, viewer}`（CHECK）。
5. **`protected ⟹ super_admin`**（CHECK，§2.3）。
6. **`protected ⟹ active`**（CHECK，§2.3；受保護者恆未封存未刪除，A）。
7. **≥1 active super_admin**（由 §2.1 的 **SSM 初始 admin 保證**，非以計數維持、不依賴 DB protected 列；`is_protected`（若使用）另由 CHECK 5＋6 硬化）。

---

## 4. Repository 層增量（讀取程式碼，非 schema）

於 `app/repositories/admin.py` 新增（**無 DDL**）：

- `list_admins(*, status: AdminStatusFilter, limit: int, offset: int) -> Sequence[Row]`：依 §2.7 謂詞（`ACTIVE`/`ARCHIVED`/`DELETED`/`ALL`）查詢，`ORDER BY id` 穩定分頁。
  - **稽核者名稱解析（L1，best practice）**：對 `archived_by` / `deleted_by`（principal_id）以**兩次 `LEFT JOIN admins`（`admins.principal_id = archived_by` / `= deleted_by`）**帶出操作者 `username`，供 API 的 `AdminSummary` 直接顯示「誰封存/刪除的」，免前端二次查詢。回傳含 `admin` 與解析出的 `archived_by_username` / `deleted_by_username`（`str | None`）。裸 `archived_by`/`deleted_by`（id）保留作穩定參照。（操作者恆為 admin principal；找不到對應 admin 列時為 `None`。）
- `count_admins(*, status: AdminStatusFilter) -> int`：同謂詞計數（供分頁 total）。
- `get_list_row(admin_id: int) -> AdminListRow | None`：**單列版 `list_admins`**——同一組兩次 `LEFT JOIN admins`、只多 `WHERE Admin.id = admin_id`。理由：`GET /admin/admins/{id}` 與四個生命週期端點都回 `AdminSummary`（含 `archived_by_username` / `deleted_by_username`），而這兩欄**不在 `Admin` model 上**、需 JOIN 解析；若單一 admin 路徑只用 `get()`（回裸 `Admin`）則 username 欄無從填。此方法讓**列表與單一 admin 共用同一套 username 解析**（DRY、單一查詢）。service 以 `get_row`（§3.9）包裝、施加軟刪除規則。

> `AdminListRow`（`@dataclass`）＝ `list_admins` / `get_list_row` 的一列：`admin` 本體 ＋ `archived_by_username` / `deleted_by_username`（`str | None`）。裸 `archived_by`/`deleted_by`（id）仍在 `admin` 上作穩定參照。

> **不需 `count_active_super_admins`**：Option B 的不變式由單列 protected 守衛保證，**無任何聚合計數或 `FOR UPDATE`**（這是相對前一版設計的關鍵簡化）。

> `AdminStatusFilter`（`active`/`archived`/`deleted`/`all`，`StrEnum`）放 `app/core/enums.py`，跨層共用。

---

## 5. 安全性考量（資料層）

- **不變式無併發異常**：≥1 super_admin 由 **SSM 初始 admin** 保證（§2.1，不碰 DB），**不存在 write skew**、不需鎖或 SERIALIZABLE；`is_protected`（若使用）亦為單列、無鎖。
- **雙保險**：app 守衛（友善訊息）＋ `ck_admins_protected_is_super`（DB 硬化）共同確保受保護者恆為 super_admin。
- **`is_protected` 不可經 API 切換**：杜絕「取消最後一個保護」重新引入聚合問題（§2.2）。
- **稽核完整性**：`*_by` 為 `SET NULL`，操作者被移除不連坐被稽核列。
- **`is_active` 防呆**：列表禁用計算屬性下 SQL filter，改時間戳謂詞（§2.7）。

---

## 6. 資料模型的 Migration

### 6.1 新 revision（append-only，接於 `c3d4e5f6a7b8` 之後）

> ⚠️ **不就地修訂 `c3d4e5f6a7b8`**：該 revision **已合併並 push 至 `origin/main`（已共享）**，依「已部署／已共享 revision 不可變」原則（admin-account-refinement §3.2），本欄位改以**新增 append-only revision** 交付（與當時 `c3d4e5f6a7b8` 仍在未合併分支、可就地修訂的情境不同）。

已交付的 migration（`e5f6a7b8c9d0`，已 push／不可變）`upgrade()`（順序有意義——先建欄、標記、最後才加 CHECK，確保加 CHECK 時所有列皆已合規）：
- `ADD COLUMN is_protected BOOLEAN NOT NULL DEFAULT false`（既有列由 `server_default` 自動填 `false`）。
- **遷移相容標記**：`UPDATE admins SET is_protected = 1 WHERE admin_role = 'super_admin' AND archived_at IS NULL AND deleted_at IS NULL`——把遷移當下**既有的** active super_admin 保守標為 protected。
  - **fresh 安裝**（新模型的常態:bootstrap 走 SSM 初始 admin、無 DB super_admin）→ 此 `UPDATE` 為 **no-op**，落地後**無任何 protected 列**，符合 §2.1「is_protected 休眠」。
  - **既有安裝**（舊 seed 曾建過 DB super_admin）→ 把當時的 super_admin 標為不可移除，屬**保守預設**（不破壞、可日後手動 `UPDATE ... SET is_protected=0` 解除）。
- `ADD CONSTRAINT ck_admins_protected_is_super CHECK (is_protected = 0 OR admin_role = 'super_admin')`。
- `ADD CONSTRAINT ck_admins_protected_is_active CHECK (is_protected = 0 OR (archived_at IS NULL AND deleted_at IS NULL))`（A）。

`downgrade()`：對稱 drop 兩條 CHECK 與 `is_protected` 欄。

> **布林方言可攜**：`server_default` 依方言取字面值（MariaDB·SQLite `0`）。測試走 SQLite `create_all`（不經 migration）→ model 加 `is_protected` 與 CHECK 即反映。真 MariaDB 驗 upgrade/downgrade。

### 6.2 bootstrap 與既有安裝

- **bootstrap（第一位 super admin）**：改由 **SSM 初始 admin**（`INITIAL_ADMIN_*`，不進 DB），登入後建立 DB admin——見 [`admin-management-service.md`](./admin-management-service.md) §3.7。**不再有 seed 腳本建 DB root**。
- **既有安裝**：§6.1 的遷移相容 `UPDATE` 已把當時的 active super_admin 保守標為 protected（如上）;若不需要,可事後手動解除。新增的 DB admin 一律 `is_protected=False`。

---

## 7. TDD 測試計畫（資料層；先寫、先看到 RED）

### 7.1 Unit — model / DB（`tests/unit/test_admin_model.py` 增補）
- `is_protected` 預設 `False`（server_default）。
- `ck_admins_protected_is_super`：`is_protected=True` 且 `admin_role != 'super_admin'` → `IntegrityError`；`is_protected=True` + `super_admin` → 可寫入。
- `ck_admins_protected_is_active`（A）：`is_protected=True` 且 `archived_at`／`deleted_at` 任一有值 → `IntegrityError`；`is_protected=True` + 兩者皆 `NULL`（active）→ 可寫入。

### 7.2 Unit — Repository（`tests/unit/repositories/test_admin.py` 增補）
- `list_admins(status=ACTIVE/ARCHIVED/DELETED/ALL)` 回對應集合；分頁 `limit/offset`、`ORDER BY id` 正確。
- `count_admins(status=...)` 與對應 `list` 筆數一致。

### 7.3 Unit — enum
- `AdminStatusFilter` 四成員字串值。

---

## 8. 已定案決策

- ✅ **「≥1 super_admin」主保證＝SSM 初始 admin**（不進 DB、恆可登入、無法鎖死）——**無聚合、無鎖、無 write skew**，比 DB 內受保護 root 更強（§2.1）。
- ✅ **`is_protected` 為可選、休眠的 DB 單列硬化**：保留欄 + 守衛 + 兩條 CHECK,但**預設無列被標 protected**（seed 已移除、API 恆建 False、無切換端點，§2.1／§2.2）。
- ✅ **兩條單列 CHECK**：`protected ⟹ super_admin` ＋ `protected ⟹ active` DB 硬化（§2.3，A）——一旦使用 is_protected 即自動生效（休眠時恆滿足）。
- ✅ **migration 保守相容標記**：`UPDATE ... WHERE super_admin AND active`;fresh 安裝 no-op（無 protected 列），既有安裝保守標記可事後解除（§6）。
- ✅ 本功能對 schema 增量＝**`is_protected` 一欄 + 兩條 CHECK + 一支 append-only migration**（誠實揭露）。
- ✅ 移除 `count_active_super_admins`（不再需要聚合計數）；保留 `list_admins`／`count_admins`（§4）。
- ✅ 不加 `updated_by`（§2.4）；`username` 不可變（§2.5）；`role` vs `admin_role` 正交（§2.6）；列表用時間戳謂詞（§2.7）。

## 9. 待確認事項（Open Questions）

1. **升降權稽核是否需專用 audit 表**（who/target/from/to/at）？本組先以 log 交付、不加表（§2.4）。
2. **是否要 transfer-ownership**（換 root）：本組 out of scope（§2.2）；若需要，另立單列鎖的原子操作規格。
3. **列表分頁**預設 `limit`／上限由 api 規格定案（§4 只定方法簽名）。
