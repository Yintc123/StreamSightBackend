# 規格書（Model 層）：Admin 管理 — 資料模型

> 狀態：**Draft（待實作）** ／ 目標版本：next+2 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
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

**一句話**：CMS 對「其他 admin 帳號」的完整生命週期管理——登入（既有）＋新增／更新名稱／升降權／改自己密碼／封存／解除封存／軟刪除／復原／列表；並以「**受保護 root**」的單列不變式保證系統永遠 ≥1 位 super_admin。

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
| 列表／明細 | `list_admins`／`get` | `GET /admin/admins`／`GET /admin/admins/{id}` |
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
> **核心決策**：§2.1「受保護 root」把「≥1 super_admin」降為單列不變式（無聚合、無鎖、無 write skew）。

---

## 1. 背景與目標

「Admin 管理」要支援對**其他 admin 帳號**的完整生命週期：**登入**（既有）、**新增**、**更新**（顯示名稱、密碼、權限等級）、**封存／解除封存**、**軟刪除／復原**、**列表查詢**，並維持一條關鍵不變式：

> **系統永遠至少保有 1 位可用的 SUPER_ADMIN**（否則無人能管理 admin，系統鎖死）。

本文回答資料模型層面：**如何用最單純、併發安全的方式支撐上述操作與該不變式。**

### 目標

- 給出 Admin 管理功能的**權威資料模型參考**（欄位、約束、狀態機、不變式）。
- **新增 `admins.is_protected`**（受保護 root 標記）+ 一支 append-only migration + 一條 `CHECK`，以把「≥1 super_admin」這個**聚合不變式降為單列不變式**（見 §2.1，這是本次設計的核心）。
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

### 2.1 用「受保護 root」把「≥1 super_admin」降為單列不變式（核心決策）

「系統至少 1 位 super_admin」若以「對 super_admin 集合計數」實作，是一個**集合基數不變式**——併發下會發生 **write skew**（兩個交易各降級／移除不同的 super_admin，各自讀到 count≥2 而放行 → 集合歸零）。防護它需要 `SELECT ... FOR UPDATE` 鎖住整個集合或 SERIALIZABLE，複雜且在 SQLite（測試）測不到。

**定案：改用「受保護 root」，把聚合不變式降為單列不變式**——不變式從此**不需要任何計數或鎖**：

- 新增 `admins.is_protected: Boolean`（`default False`）。**seed 建立的 bootstrap admin 設 `is_protected = True`**。
- 配合 service 層**單列守衛**（見 [`admin-management-service.md`](./admin-management-service.md) §3.5，皆只讀 target 自己那一列）：
  - **受保護 admin 不可被降級／封存／軟刪除**。
  - （承使用者定案的工作流規則）**任何 super_admin 不可被直接封存／軟刪除**，須先 `set_admin_role` 降為 editor／viewer 才能封存／刪除。
- **不變式的結構性證明**：`is_protected` 的 root 恆存在（seed 建立）∧ 受保護者不可降級（守衛）∧ 受保護者不可封存／刪除（守衛）∧ seed 建它為 `super_admin`（＋下方 CHECK 鎖死 `protected ⟹ super_admin`）→ **root 永遠是 active super_admin**。故 super_admin 數**永遠 ≥ 1**，且**全程只需單列判斷、零聚合、零鎖、零 write skew**。

> **這是「把全域不變式降成單列不變式」的標準工程手法**：能降就降——單列不變式沒有併發異常、可決定性測試。此模型即 GitHub org owner / GCP Organization owner / AWS root 帳號的做法：有一個結構上不可移除的擁有者，其餘 super_admin 可自由增刪。

> **代價（誠實揭露）**：這使本功能**不再是「零 schema 增量」**——需 **+1 欄 `is_protected` + 1 支 migration**。此取捨划算：以「一個布林欄」換掉「整套集合鎖 + 併發推理 + SQLite 測不到的 race」，淨簡化且更貼近真實平台做法。

### 2.2 `is_protected` 由 seed 設定、**管理 API 不可切換**（避免又變回聚合問題）

- `is_protected` **只在 seed 建立 root 時設 `True`**；**管理 API 一律建立 `is_protected=False` 的一般 admin、且不提供切換 `is_protected` 的端點**。
- 理由：若允許任意「取消保護」，就會冒出「不可取消最後一個受保護者」這種**新的聚合不變式** → write skew 回歸。故最單純安全的定案是 **`is_protected` 建立後不可變（seed-only）**。
- **允許多個受保護 root**（更穩健）：`is_protected` 無唯一約束；只要**曾有 ≥1 個**受保護 super_admin（seed 保證），且受保護者不可移除，不變式即恆成立——**無需「恰好一個」的基數約束**（那又會是聚合問題）。
- **繼任（transfer ownership）** 屬 out of scope；若未來需要「換 root」，另立一個**專屬、單列鎖**的原子操作（把 `is_protected` 由舊 root 搬到新 root，鎖當前 root 單列即可，屬 lost-update 範疇、非 write skew）。本組不提供。

> **系統前置條件**：正確性依賴 **seed 在 bootstrap 時建立至少一位 `is_protected=True` 的 super_admin**。fresh 安裝由 seed（冪等）保證；既有安裝以一次性資料步驟把 bootstrap admin 標為 protected（見 §3.6 migration 註）。

### 2.3 `CHECK(protected ⟹ super_admin)`（單列整合性硬化）

新增 `CheckConstraint("is_protected = 0 OR admin_role = 'super_admin'", name="ck_admins_protected_is_super")`：DB 層保證**受保護者必為 super_admin**（integrity-first，對齊既有 `ck_admins_admin_role` 風格）。搭配「受保護者不可降級」的 app 守衛（防呆友善訊息），形成雙保險——即使繞過 service，DB 也擋下「把 protected 改成非 super_admin」或「建立 protected 的非 super_admin」。

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
| **`is_protected`** | **`Boolean`** | **`default False` / `server_default false`（本組新增）** | **受保護 root 標記（seed-only、不可經 API 切換，§2.2）** |
| `archived_at` / `archived_by` | `DateTime(tz)` / `int` nullable | `by` FK→principals `SET NULL` | 封存（成對稽核） |
| `deleted_at` / `deleted_by` | `DateTime(tz)` / `int` nullable | `by` FK→principals `SET NULL` | 軟刪除（成對稽核） |
| `created_at` / `updated_at` | `DateTime(tz)` | Base（`onupdate`） | |

### 3.2 約束（`__table_args__`）

既有：`fk_admins_principal_role`（複合 FK, CASCADE）、`ck_admins_role_admin`（role=1）、`ck_admins_admin_role`（admin_role 值域）、`ix_admins_principal_id`、`ix_admins_username`。

**新增**：
- `CheckConstraint("is_protected = 0 OR admin_role = 'super_admin'", name="ck_admins_protected_is_super")`（受保護者必為 super_admin，§2.3）。

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

1. **成對稽核**：`archived_at IS NULL ⟺ archived_by IS NULL`；`deleted_at IS NULL ⟺ deleted_by IS NULL`。
2. **`role` 恆 1**（型別判別子）。
3. **`username` 唯一且不可變**；軟刪除者永久保留其 username（不 purge）。
4. **`admin_role` 值域** ∈ `{super_admin, editor, viewer}`（CHECK）。
5. **`protected ⟹ super_admin`**（CHECK，§2.3）。
6. **≥1 active super_admin**（由 §2.1 的 protected root **結構性保證**，非以計數維持）。

---

## 4. Repository 層增量（讀取程式碼，非 schema）

於 `app/repositories/admin.py` 新增（**無 DDL**）：

- `list_admins(*, status: AdminStatusFilter, limit: int, offset: int) -> Sequence[Row]`：依 §2.7 謂詞（`ACTIVE`/`ARCHIVED`/`DELETED`/`ALL`）查詢，`ORDER BY id` 穩定分頁。
  - **稽核者名稱解析（L1，best practice）**：對 `archived_by` / `deleted_by`（principal_id）以**兩次 `LEFT JOIN admins`（`admins.principal_id = archived_by` / `= deleted_by`）**帶出操作者 `username`，供 API 的 `AdminSummary` 直接顯示「誰封存/刪除的」，免前端二次查詢。回傳含 `admin` 與解析出的 `archived_by_username` / `deleted_by_username`（`str | None`）。裸 `archived_by`/`deleted_by`（id）保留作穩定參照。（操作者恆為 admin principal；找不到對應 admin 列時為 `None`。）
- `count_admins(*, status: AdminStatusFilter) -> int`：同謂詞計數（供分頁 total）。

> **不需 `count_active_super_admins`**：Option B 的不變式由單列 protected 守衛保證，**無任何聚合計數或 `FOR UPDATE`**（這是相對前一版設計的關鍵簡化）。

> `AdminStatusFilter`（`active`/`archived`/`deleted`/`all`，`StrEnum`）放 `app/core/enums.py`，跨層共用。

---

## 5. 安全性考量（資料層）

- **不變式無併發異常**：≥1 super_admin 由單列 protected 守衛保證，**不存在 write skew**，也不需鎖或 SERIALIZABLE（§2.1）。
- **雙保險**：app 守衛（友善訊息）＋ `ck_admins_protected_is_super`（DB 硬化）共同確保受保護者恆為 super_admin。
- **`is_protected` 不可經 API 切換**：杜絕「取消最後一個保護」重新引入聚合問題（§2.2）。
- **稽核完整性**：`*_by` 為 `SET NULL`，操作者被移除不連坐被稽核列。
- **`is_active` 防呆**：列表禁用計算屬性下 SQL filter，改時間戳謂詞（§2.7）。

---

## 6. 資料模型的 Migration

### 6.1 新 revision（append-only，接於 `c3d4e5f6a7b8` 之後）

> ⚠️ **不就地修訂 `c3d4e5f6a7b8`**：該 revision **已合併並 push 至 `origin/main`（已共享）**，依「已部署／已共享 revision 不可變」原則（admin-account-refinement §3.2），本欄位改以**新增 append-only revision** 交付（與當時 `c3d4e5f6a7b8` 仍在未合併分支、可就地修訂的情境不同）。

`upgrade()`：
- `ADD COLUMN is_protected BOOLEAN NOT NULL DEFAULT false`（既有列由 `server_default` 自動填 `false`）。
- `ADD CONSTRAINT ck_admins_protected_is_super CHECK (is_protected = 0 OR admin_role = 'super_admin')`。

`downgrade()`：對稱 drop CHECK 與 `is_protected` 欄。

> **布林方言可攜**：比照既有 migration，`server_default` 依方言取字面值（PostgreSQL `false`／MySQL·MariaDB·SQLite `0`）。測試走 SQLite `create_all`（不經 migration）→ model 加 `is_protected` 與 CHECK 即反映。真 MariaDB 驗 upgrade/downgrade。

### 6.2 既有安裝的 root 標記（資料步驟）

- **fresh 安裝**：seed 建 bootstrap admin 時直接 `is_protected=True`（見 [`admin-management-service.md`](./admin-management-service.md) create 增參與 seed）。
- **既有安裝**（已有 bootstrap super_admin）：升級後以一次性資料步驟把該 bootstrap admin 標為 `is_protected=True`（或重跑冪等 seed）。此步驟屬營運，非 schema。

---

## 7. TDD 測試計畫（資料層；先寫、先看到 RED）

### 7.1 Unit — model / DB（`tests/unit/test_admin_model.py` 增補）
- `is_protected` 預設 `False`（server_default）。
- `ck_admins_protected_is_super`：`is_protected=True` 且 `admin_role != 'super_admin'` → `IntegrityError`；`is_protected=True` + `super_admin` → 可寫入。

### 7.2 Unit — Repository（`tests/unit/repositories/test_admin.py` 增補）
- `list_admins(status=ACTIVE/ARCHIVED/DELETED/ALL)` 回對應集合；分頁 `limit/offset`、`ORDER BY id` 正確。
- `count_admins(status=...)` 與對應 `list` 筆數一致。

### 7.3 Unit — enum
- `AdminStatusFilter` 四成員字串值。

---

## 8. 已定案決策

- ✅ **受保護 root**：新增 `admins.is_protected`，把「≥1 super_admin」由聚合不變式降為**單列不變式**——**無聚合、無鎖、無 write skew**（§2.1）。
- ✅ **`is_protected` seed-only、API 不可切換、可多個**（避免重新引入聚合問題，§2.2）。
- ✅ **`CHECK(protected ⟹ super_admin)`** DB 硬化（§2.3）。
- ✅ 本功能對 schema 增量＝**`is_protected` 一欄 + 一支 append-only migration**（誠實揭露，取捨划算）。
- ✅ 移除 `count_active_super_admins`（不再需要聚合計數）；保留 `list_admins`／`count_admins`（§4）。
- ✅ 不加 `updated_by`（§2.4）；`username` 不可變（§2.5）；`role` vs `admin_role` 正交（§2.6）；列表用時間戳謂詞（§2.7）。

## 9. 待確認事項（Open Questions）

1. **升降權稽核是否需專用 audit 表**（who/target/from/to/at）？本組先以 log 交付、不加表（§2.4）。
2. **是否要 transfer-ownership**（換 root）：本組 out of scope（§2.2）；若需要，另立單列鎖的原子操作規格。
3. **列表分頁**預設 `limit`／上限由 api 規格定案（§4 只定方法簽名）。
