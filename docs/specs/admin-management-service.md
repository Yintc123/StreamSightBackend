# 規格書（Service 層）：Admin 管理 — 業務邏輯

> 🔄 **變更註記（初始 admin 整併，晚於本文其餘內容）**：seed 腳本已**移除**；**第一位 super admin 改為 SSM-backed「初始 admin」**（`INITIAL_ADMIN_USERNAME` + `INITIAL_ADMIN_PASSWORD_HASH`；憑證存 config／SSM、**不進 DB**、哨兵 `principal_id=0`、只發 access token、不可改密碼／鎖死；`app/services/initial_admin.py`）。影響:
> - **「≥1 active super_admin」改由 SSM 初始 admin 保證**（恆可登入、無法被鎖死），取代原「seed 建 DB 受保護 root」;`is_protected` 機制保留為**可選** DB 硬化,預設無列被標 protected。
> - **bootstrap 流程**:設定 SSM 的 `INITIAL_ADMIN_*` → 以它登入 → 建立 DB admin。
> - §3.7（Seed）、§6（復原）已據此改寫;其餘提及 seed／受保護 root 之處以本註記為準。

> 狀態：**Draft（待實作）** ／ 目標版本：next+2 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「Admin 管理」三份規格的 **Service 層**（業務邏輯）。另兩份：
> - [`admin-management-model.md`](./admin-management-model.md)（資料模型層）
> - [`admin-management-api.md`](./admin-management-api.md)（HTTP API 層）
>
> 🧭 **功能總覽（入口）見 [`admin-management-model.md`](./admin-management-model.md) §0**。
>
> 🔗 交付 [`admin-account-refinement.md`](./admin-account-refinement.md) §1／§11.1 與 [`rbac.md`](./rbac.md) §11.3 延遲的「Admin 管理」業務能力，並承接 [`rbac.md`](./rbac.md) §5.1 的「升降權業務入口」。**前置依賴**：rbac（next+1）的 `require_min_admin_role` 與 `grade` 需先落地。
>
> ⚠️ **既有能力（[`admin-account-refinement.md`](./admin-account-refinement.md) §5.3，不重寫）**：`create` / `get(include_deleted)` / `get_by_username` / `get_by_principal_id` / `archive` / `unarchive` / `delete` / `restore`；`AuthService.admin_login`。本文**新增** `update` / `change_password`（自助） / `set_admin_role` / `list_admins`，並在 `create`／`archive`／`delete` 疊加**單列守衛**（受保護 root、super_admin 須先降級、禁對自己）。**不含** super_admin 重設他人密碼（`reset_password`）。

---

## 1. 背景與目標

補齊 Admin 管理的**更新面**與**安全守衛**。核心設計是**受保護 root**（見 [`admin-management-model.md`](./admin-management-model.md) §2.1）：把「系統至少 1 位 super_admin」由聚合不變式**降為單列不變式**——所有守衛皆只讀 target 自己那一列，**無計數、無鎖、無 write skew**。

### 目標

- 新增 service 方法：`update`（改 `name`）、`change_password`（自助、驗舊）、`set_admin_role`（升降權）、`list_admins`。**不提供 `reset_password`**（super_admin 重設他人密碼）——目前不規劃此功能。
- `create` 增參 `is_protected: bool = False`（管理 API 一律傳 `False`；`is_protected=True` 為可選 DB 硬化，預設無列被標 protected）。
- 疊加**單列守衛**（authoritative，放 service）：
  - **受保護 admin 不可降級／封存／軟刪除**（保證 ≥1 super_admin，§3.5）。
  - **任何 super_admin 不可被直接封存／軟刪除**——須先 `set_admin_role` 降為 editor／viewer（使用者定案的兩步工作流，§3.5）。
  - **禁止對自己 archive／delete**、**禁止自我提權**。
- **密碼變更連帶撤銷該 principal 全部 refresh token**（強制重新登入）。

### 非目標（Out of scope）

- **HTTP／DTO／授權 dependency**→ [`admin-management-api.md`](./admin-management-api.md)。授權「機制」屬 [`rbac.md`](./rbac.md)。
- **schema／migration**：新增一欄 `is_protected` + 一支 append-only migration，詳見 [`admin-management-model.md`](./admin-management-model.md) §2.1／§6（本文只**使用** `is_protected`）。
- **transfer ownership（換 root）**、**purge**、**改 username**：皆不提供。
- **User 側**：不動。

---

## 2. 共用元件

### 2.1 `AdminStatusFilter` enum（`app/core/enums.py`，本組新增）
`ACTIVE` / `ARCHIVED` / `DELETED` / `ALL`（`StrEnum`）——供 `list_admins`、repository、api 查詢參數共用。

### 2.2 時間／密碼慣例
- 時間戳 `datetime.now(UTC)`（aware-UTC）。
- 密碼 hash 走 `hash_password`（argon2id）；自助改密碼驗舊密碼走 `verify_password`。
- 密碼強度 `min_length=8, max_length=128`（DTO 邊界驗；service 收到的已合法）。

### 2.3 `actor_principal_id`（操作者稽核）
「改動他人帳號」的方法帶 `actor_principal_id`（端點傳 `current_admin.principal_id`）：狀態轉移成對寫 `archived_by`／`deleted_by`；update／set_admin_role 以 log 記 `actor → target`（不加 `*_by` 欄，承 model §2.4）。沿用既有 `archive`／`delete` 的 `actor_principal_id: int | None = None`（系統呼叫時傳 `None`，如 bootstrap 腳本）。

---

## 3. Service 方法

> 交易邊界：每個變更方法持有唯一一次 commit。失敗 rollback。

### 3.1 `create`（增參 `is_protected`）

```python
async def create(
    self, username: str, name: str, password: str,
    admin_role: AdminRole = AdminRole.VIEWER,
    is_protected: bool = False,          # 【本組新增】可選 DB 硬化，管理 API 一律傳 False
) -> Admin
```

- 沿用既有正規化／格式驗證／查重／argon2（admin-account-refinement §5.3）。
- 落地 `is_protected`（預設 `False`）。**管理 API 一律傳 `False`**（不開放建立受保護 admin）；`is_protected=True` 保留為可選 DB 硬化機制，預設無列被標 protected（≥1 active super_admin 不變式由 SSM 初始 admin 保證，§3.7）。
- **`CHECK(protected ⟹ super_admin)`**（model §2.3）：若 `is_protected=True` 但 `admin_role != SUPER_ADMIN` → `IntegrityError`。

### 3.2 `update`（更新顯示名稱）

```python
async def update(self, admin_id: int, *, name: str, actor_principal_id: int) -> Admin
```

- `get`（軟刪除者 → `NotFoundError`）→ 設 `name`（非空、1–100，DTO 驗）→ commit → 回。**不接受 username**（不可變）。**不撤 token**（改名不影響認證）。log `actor → target`。

> 升降權**不走這裡**（見 `set_admin_role` §3.4）；密碼**不走這裡**（見 §3.3）——各有專屬守衛與稽核語意。

### 3.3 `change_password`（自助改自己密碼，需舊密碼）

> **範圍定案**：**只提供自助改密碼**；**不提供 super_admin 重設他人密碼**（`reset_password` 已移除）。故無 super_admin 對他人密碼的操作路徑，也**不需** step-up 再認證的討論（原審查 L5 因功能移除而消解）。復原路徑見 §6 安全性考量。

變更密碼後**撤銷該 admin 全部 refresh token**（`revoke_all_for_principal(principal_id, now)`），強制所有裝置重新登入。

```python
async def change_password(self, admin_id: int, *, current_password: str, new_password: str) -> None
```
- `get`（未刪除）→ 驗舊密碼 `verify_password(current_password, admin.password_hash)`，不符 → `UnauthorizedError`（統一訊息）。此路徑帳號必存在（呼叫者本人、已過 `get_current_admin`），故直接 `verify_password`、不需 dummy。
- 新密碼不得等於舊 → `BadRequestError`（見 §10 Open Q1）。**判定方式**：以 `verify_password(new_password, admin.password_hash)` 對**舊 hash** 驗新明文（argon2 隨機 salt 無法直接比 hash），為真即「新＝舊」→ 擋（多一次 argon2 verify，可接受）。
- 設新 hash → `revoke_all_for_principal`（連自己當前 token 一併撤）→ commit。**無 `actor_principal_id`**（actor 即本人）。

### 3.4 `set_admin_role`（升降權；命名刻意用 `admin_role` 全名，避免與型別判別子 `role` 混淆）

> **命名（M1）**：方法名為 **`set_admin_role`**（非 `set_role`），對應它改的是 `admins.admin_role`（權限等級），**不是** `role`（型別判別子）。[`rbac.md`](./rbac.md) §5.1 的 `set_role` 同步更名為 `set_admin_role`。

```python
async def set_admin_role(
    self, admin_id: int, *, admin_role: AdminRole, actor_principal_id: int
) -> Admin
```

執行順序（**H2：idempotent 先於守衛**）：

1. `get`（軟刪除者 → `NotFoundError`）。
2. **idempotent early-return**：`if admin.admin_role == admin_role.value: return admin`（等級未變 → 直接回，**在任何守衛之前**，避免「對受保護 root 設回 super_admin」被守衛誤擋）。
3. **受保護守衛**（單列）：若 `admin.is_protected` 且 `admin_role != SUPER_ADMIN`（即嘗試降級受保護 root）→ `BusinessRuleError`（422，「cannot demote the protected root admin」）。
4. **自我提權守衛**（單列）：若 `actor_principal_id == admin.principal_id` 且 `ADMIN_ROLE_RANK[admin_role] > ADMIN_ROLE_RANK[AdminRole(admin.admin_role)]` → `BusinessRuleError`（422，「cannot elevate your own role」；**M2：業務規則統一 422**，非 403）。
5. 設 `admin.admin_role = admin_role.value` → commit → 回。log `actor` 把 `target` 由 `old` 改 `new`。
6. **不撤 token**：授權讀 child 現值（[`rbac.md`](./rbac.md) §5.3）→ 降權對後端授權**即時生效**；`grade` claim 陳舊 ≤ 一個 TTL、由 refresh 自動刷新（見 §9 Open Q2）。

> **無聚合守衛**：因「≥1 super_admin」由受保護 root 保證（model §2.1），此處**不需**「最後一位 super_admin」計數／鎖——降級一個**非受保護**的 super_admin 永遠安全（root 還在）。

### 3.5 安全不變式（單列守衛，authoritative；放 service，繞過 api 亦安全）

全部只讀 **target 自己那一列**——無聚合、無鎖、無 write skew。

| 守衛 | 判斷（單列） | 施加於 | 違反 |
|---|---|---|---|
| **受保護** | `admin.is_protected` | 降級（set_admin_role→非 super_admin）／archive／delete | `BusinessRuleError` 422 |
| **super_admin 須先降級** | `admin.admin_role == 'super_admin'` | archive／delete | `BusinessRuleError` 422（「demote before archiving/deleting a super admin」） |
| **禁對自己** | `actor_principal_id == admin.principal_id` | archive／delete | `BusinessRuleError` 422 |
| **禁自我提權** | `actor==target 且新等級 rank 更高` | set_admin_role | `BusinessRuleError` 422 |

> **DB 兜底（A）**：受保護守衛除 app 判斷外，另有兩條單列 CHECK 兜底（model §2.3）——`ck_admins_protected_is_super`（間接擋「降級受保護者」）與 `ck_admins_protected_is_active`（擋「封存／軟刪除受保護者」）。即使繞過 service 直接寫 DB，`IntegrityError` 也擋下。**app 守衛負責友善訊息（422）、DB CHECK 負責結構兜底**——「root 恆 active super_admin」除 bootstrap 外全由 DB 保證。

> **自我降級（foot-gun，刻意允許）**：自我提權被守衛擋，但**自我降級不擋**——非受保護 super_admin 可把自己降為 editor／viewer 而失去管理能力。因 protected root 仍能把他救回、系統不會鎖死，故接受此**可恢復**的 foot-gun（前端可對「降自己」加二次確認）。受保護 root 則因受保護守衛＋CHECK 無法自我降級。

**M3：守衛適用範圍（actor 為 None 時）**：
- **受保護守衛** 與 **super_admin-須先降級守衛**：**恆適用**（`actor=None` 也不能刪掉受保護 admin 或直接刪 super_admin）。
- **禁對自己／禁自我提權**：僅在**有 actor**（`actor==target` 可比對）時適用；`actor=None`（script）→ 不適用。

**與 idempotency 的順序**（承 admin-account-refinement 既有 `archive`/`unarchive`/`restore` 的 idempotent）：**先判「是否已在目標態（idempotent 回）」→ 再判守衛 → 再執行轉移**。例：對已封存的 editor 再 `archive` → idempotent 成功，不必再過守衛。

### 3.6 `archive` / `delete`（既有方法疊加 §3.5 守衛）

以 `delete` 為例（`archive` 同型）：

```python
async def delete(self, admin_id: int, *, actor_principal_id: int | None = None) -> None:
    admin = await self.get(admin_id)                       # 已軟刪 → NotFoundError
    # 守衛（單列；順序：先 idempotent 不適用於 delete → 直接守衛）
    if admin.is_protected:
        raise BusinessRuleError("cannot delete the protected root admin")
    if admin.admin_role == AdminRole.SUPER_ADMIN.value:
        raise BusinessRuleError("demote before deleting a super admin")
    if actor_principal_id is not None and actor_principal_id == admin.principal_id:
        raise BusinessRuleError("cannot delete yourself")
    now = datetime.now(UTC)
    admin.deleted_at, admin.deleted_by = now, actor_principal_id
    await self.refresh_repo.revoke_all_for_principal(admin.principal_id, now)
    await self.session.commit()
```

`archive` 同：`is_protected` / `admin_role==super_admin` / 對自己 → 422；idempotent（已封存 → 直接回，**在守衛前**，因已封存的 editor 再 archive 應成功）。

> **注意順序（archive）**：`archive` 有 idempotent（已封存直接回）。順序為：`get` → **若已 `archived_at` 直接回** → 守衛 → 設值。但因 super_admin 根本不能進到 archived 態（archive 前就被守衛擋），實務上已封存者必非 super_admin，兩者不衝突。

### 3.7 初始 super admin（SSM，取代 seed）

**seed 腳本已移除。** 第一位 super admin 是 **SSM-backed 初始 admin**（`app/services/initial_admin.py`）:憑證存 config／SSM（`INITIAL_ADMIN_USERNAME` + `INITIAL_ADMIN_PASSWORD_HASH`，argon2id 雜湊;另有**可選** `INITIAL_ADMIN_NAME` 顯示名,空 → 用 username）、**不進 DB**、哨兵 `principal_id=0`、登入只發 access token、合成一個記憶體 `Admin(super_admin)`。bootstrap 流程:設定 SSM → 以它登入 → 經 `POST /admin/admins` 建立 DB admin。

- 它**恆可登入、無法被鎖死**,故「≥1 active super_admin」不變式改由**它**保證（取代原 seed 建的 DB 受保護 root，model §2.1 變更註記）。
- **不可經 API 改密碼**（`change_password` 對哨兵 id → `ForbiddenError`）;輪替＝更新 SSM 雜湊,停用＝清空 config。
- 其 username 為**保留字**:禁止用 API 建立同名 DB admin（`create` → `ConflictError`）。

### 3.8 `list_admins`（委派 repository）

```python
async def list_admins(
    self, *, status: AdminStatusFilter = AdminStatusFilter.ACTIVE, limit: int = 50, offset: int = 0,
) -> tuple[Sequence[AdminListRow], int]
```
委派 `repo.list_admins` + `repo.count_admins`，回 `(rows, total)`。純讀取、無 commit。

> **回傳型別＝`AdminListRow`（非裸 `Admin`）**：對齊 model §4 的 L1 稽核者名稱解析——`rows` 每列帶 `admin` 本體 ＋ 解析出的 `archived_by_username` / `deleted_by_username`，供 api `AdminSummary` 直接顯示「誰封存/刪除」（免前端二次查詢）。若回裸 `list[Admin]` 則 `AdminSummary` 的 username 欄無從填，故型別必須是 `AdminListRow`。

### 3.9 `get_row`（明細／生命週期回身的單列 `AdminListRow`）

```python
async def get_row(self, admin_id: int, *, include_deleted: bool = False) -> AdminListRow
```
`GET /admin/admins/{id}` 與四個生命週期端點都回 `AdminSummary`，而 `AdminSummary` 含 `archived_by_username` / `deleted_by_username`——這些**不在 `Admin` model 上**、需 JOIN 解析。故單一 admin 路徑**不能只用 `get()`（回裸 `Admin`）**，改委派 `repo.get_list_row(admin_id)`（與 `list_admins` **同一組 LEFT JOIN**、只多 `WHERE id=?`），回帶 username 的單列 `AdminListRow`。軟刪除規則同 `get`（未帶 `include_deleted` 時軟刪 → `NotFoundError`）。這讓**列表與單一 admin 走同一套 username 解析**（DRY、單一查詢，優於每列多打 `get_by_principal_id`）。

---

## 4. 依賴注入

`AdminService.__init__` 既有 `repo` / `principal_repo` / `refresh_repo` 已足夠；本組新方法不需額外注入。

---

## 5. 流程圖

```
建立（seed 建 root）：
  create(..., SUPER_ADMIN, is_protected=True)   # 管理 API 一律 is_protected=False
  → CHECK(protected ⟹ super_admin) 硬化

改名 / 改密碼：
  update(name)         → get → set name → commit（不撤 token）
  change_password(cur,new) → get → verify(cur)✔ → new==cur? 擋 → set hash → revoke_all → commit
  （不提供 super_admin 重設他人密碼）

升降權（單列守衛、無鎖）：
  set_admin_role(role) → get → [等級未變? 回] → protected? 擋 → 自我提權? 擋 → set → commit（不撤 token）

封存 / 軟刪除（單列守衛、無鎖）：
  archive/delete → get → [archive:已封存? 回] → protected? 擋 → super_admin? 擋(先降級) → 對自己? 擋
                → set archived_at/deleted_at + by → revoke_all → commit

移除 super_admin 的正解（兩步、皆單列安全）：
  set_admin_role(super_admin → editor/viewer)  ─►  archive / delete
```

---

## 6. 安全性考量

- **不變式無併發異常**：≥1 super_admin 由受保護 root **單列**保證，**無 write skew、無鎖、無 SERIALIZABLE**（model §2.1）——這是本設計相對「聚合守衛」版本的關鍵優勢。
- **兩步移除 super_admin**：先降級再封存／刪除，是刻意的 deliberate action，防手滑一步誤除高權限帳號。
- **密碼變更撤 token**：自助改密碼後強制所有裝置重新登入（既有 refresh token 立即失效；access token 殘留 ≤ TTL，既有取捨）。
- **忘記密碼的復原路徑**：admin **無 email**（找不回）、且**不提供 super_admin 重設他人密碼**。
  - **一般 admin／非受保護 super_admin** 被鎖在外：由另一位 super_admin 復原——非 super_admin 直接軟刪後重建；super_admin 先降級再軟刪後重建。此為目前定案下明確接受的取捨。
  - **所有 DB super_admin 都失效 → 由 SSM 初始 admin 復原（已實作）**：`app/services/initial_admin.py` 的初始 admin（§3.7）**憑證存 config／SSM、不進 DB**、哨兵 `principal_id=0`、只發 access token、恆為 `super_admin`、**不出現在列表、無法被封存／刪除／改名／改密碼／鎖死**;只要 SSM 有其 argon2 雜湊就永遠可登入。故即使 DB 裡的 super_admin 全被鎖/刪,仍能以初始 admin 登入、重建或修復 DB admin——它同時是 bootstrap 入口與永久復原路徑。
    - 憑證:`INITIAL_ADMIN_USERNAME` + `INITIAL_ADMIN_PASSWORD_HASH`（argon2id 雜湊,SSM SecureString;兩者皆非空才啟用）。**明文密碼永不落地任何設定**。
    - 密碼「輪替」= 更新 SSM 的雜湊（改雜湊即令舊 access token 於 ≤ 一個 TTL 後失效;停用 = 清空 config → 舊 token 立即 401）。
    - 初始 admin username 為**保留字**:禁止用 API 建立同名 DB admin（避免遮蔽/混淆）。
- **降權即時性**：授權讀 child 現值 → 降權對後端存取控制即時；`grade` claim 由 refresh 刷新。
- **稽核**：狀態轉移寫 `*_by`；更新／升降權／重設密碼走 log（不記明文）。

---

## 7. TDD 測試計畫（先寫、先看到 RED）

### 7.1 Unit — update
- `update(name=...)` 改名、`updated_at` 前進、不撤 token；對軟刪除者 → `NotFoundError`。

### 7.2 Unit — 密碼（僅自助）
- `change_password`：舊正確 → 換新 + 撤 token；舊錯 → `UnauthorizedError` 不變更；新==舊 → `BadRequestError`。

### 7.3 Unit — set_admin_role
- 升 editor→super_admin（由他人操作）→ 生效、不撤 token。
- 降**非受保護** super_admin → editor → 生效（root 仍在，無需計數）。
- 降**受保護 root** → `BusinessRuleError`（422）。
- 自我提權（actor==target 升等）→ `BusinessRuleError`（422）。
- idempotent：設成相同等級 → 直接回、無副作用（含對受保護 root 設回 super_admin → 成功不擋，驗證 H2 順序）。

### 7.4 Unit — archive / delete 守衛
- archive／delete **受保護 root** → 422；archive／delete **任何 super_admin**（未降級）→ 422（先降級）。
- 先 `set_admin_role(super_admin→viewer)` 再 archive／delete → 成功。
- archive／delete 對自己（actor==target）→ 422；`actor=None`（script）→ 自我守衛不適用，但受保護／super_admin 守衛仍擋（M3）。
- 已封存的 editor 再 archive → idempotent 成功。
- **DB 兜底（A）**：繞過 service、直接對受保護 root 設 `archived_at`／`deleted_at` 並 flush → `IntegrityError`（`ck_admins_protected_is_active`，model §7.1）。

### 7.5 Unit — model / repo / seed
- `is_protected` 預設 False；`CHECK(protected ⟹ super_admin)` 生效（model §7.1）。
- `list_admins`／`count_admins` 各狀態、分頁（model §7.2）。
- seed 建立的 root：`is_protected=True`、`admin_role=super_admin`。

---

## 8. 實作順序（TDD 里程碑）

0. `is_protected` 欄 + `CHECK` + append-only migration + `AdminStatusFilter` + repo `list_admins`／`count_admins`（見 [`admin-management-model.md`](./admin-management-model.md) §4／§6／§7）。
1. `create` 增參 `is_protected` + seed 建 root 傳 `True`（7.5）。
2. `update`（改 name）（7.1）。
3. `change_password`（自助、撤 token）（7.2）。
4. `set_admin_role` + idempotent 順序 + 受保護／自我提權守衛（7.3）。
5. `archive`／`delete` 疊加受保護／super_admin-須先降級／禁對自己守衛（7.4）。
6. `list_admins`（委派 repo）。
7. 提交前檢查全綠；真 MariaDB 驗 `is_protected` migration upgrade/downgrade。

---

## 9. 已定案決策

- ✅ **受保護 root 單列守衛**保證 ≥1 super_admin：**無聚合計數、無鎖、無 write skew**（核心，model §2.1）。
- ✅ **DB 兜底（A）**：受保護守衛另有 `ck_admins_protected_is_super`＋`ck_admins_protected_is_active` 兩條 CHECK，繞過 service 直接寫 DB 亦擋（model §2.3）——「root 恆 active super_admin」除 bootstrap 外全由 DB 保證。
- ✅ **初始 admin ＝ SSM-backed（已實作，取代 seed）**：第一位 super admin 憑證存 config／SSM、不進 DB、哨兵 `principal_id=0`、只發 access token、不可被管理／改密碼／鎖死;同時是 bootstrap 入口與永久復原路徑（§3.7/§6）。「≥1 active super_admin」改由它保證。`is_protected` 機制保留為可選 DB 硬化。
- ✅ **super_admin 須先降級才能封存／刪除**（兩步工作流）；**受保護 root 不可降級／封存／刪除**。
- ✅ 新增 `update`（僅 name）／`change_password`（自助驗舊）／`set_admin_role`（升降權）／`list_admins`；`create` 增參 `is_protected`。**不提供 `reset_password`**（重設他人密碼）。
- ✅ **密碼變更撤該 principal 全部 refresh token**；改名／升降權**不撤**。
- ✅ **H2**：`set_admin_role` idempotent early-return 置於守衛之前；封存亦 idempotent 先行。
- ✅ **M1**：命名 `set_admin_role`（同步 rbac.md）。**M2**：業務規則違反統一 `BusinessRuleError`（422），403 專留授權層。**M3**：受保護／super_admin 守衛恆適用；禁對自己／自我提權僅在有 actor 時適用。
- ✅ 稽核：狀態轉移寫 `*_by`，其餘走 log（不加 `updated_by`，model §2.4）。

## 10. 待確認事項（Open Questions）

1. **`change_password` 是否強制新舊不同**：本文建議擋（`BadRequestError`）；不需可移除。
2. **降權是否也撤 token**：本文採「不撤」（授權即時、grade 由 refresh 刷新）。若要求降權當下連 UI 即時失效，改「降權亦撤 refresh token」——待確認。
3. **升降權稽核表**（承 model §8.1）。
4. **transfer ownership（換 root）**：本組不提供（model §2.2）；若需要，另立單列鎖的原子操作。
5. **reset-others 密碼 / admin email 找回**：**目前不規劃**（§3.3、§6）——一般帳號復原走「軟刪除後重建」。若鎖帳復原痛點浮現，再議 super_admin 重設他人密碼（含 step-up 再認證）或 admin email 找回機制。
6. ~~**受保護 root 的受控 break-glass（C）**~~ → **已實作為 SSM 初始 admin**（§3.7/§6/§9）:憑證存 SSM(argon2 雜湊)、不進 DB、恆可登入的 super_admin,取代 seed 並兼任復原路徑。原「離線 DBA 直接改 DB」降為最後手段。
