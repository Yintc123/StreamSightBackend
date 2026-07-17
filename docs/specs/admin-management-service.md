# 規格書（Service 層）：Admin 管理 — 業務邏輯

> 狀態：**Draft（待實作）** ／ 目標版本：next+2 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「Admin 管理」三份規格的 **Service 層**（業務邏輯）。另兩份：
> - [`admin-management-model.md`](./admin-management-model.md)（資料模型層）
> - [`admin-management-api.md`](./admin-management-api.md)（HTTP API 層）
>
> 🔗 交付 [`admin-account-refinement.md`](./admin-account-refinement.md) §1／§11.1 與 [`rbac.md`](./rbac.md) §11.3 延遲的「Admin 管理」業務能力，並**承接 [`rbac.md`](./rbac.md) §5.1** 所述「`set_role` 的業務入口由後續 admin 管理規格提供」。
>
> ⚠️ **既有能力（[`admin-account-refinement.md`](./admin-account-refinement.md) §5.3 已交付，不重寫）**：`AdminService.create` / `get(include_deleted)` / `get_by_username` / `get_by_principal_id` / `archive` / `unarchive` / `delete`（軟刪除）/ `restore`；`AuthService.admin_login`（username + 常數時間 verify）。本文只**新增** `update` / `change_password` / `reset_password` / `set_role` / `list_admins`，並在既有 `archive`／`delete`／`set_role` 上**疊加安全不變式**（最後一位 super_admin、禁對自己）。

---

## 1. 背景與目標

「Admin 管理」的 HTTP 端點（[`admin-management-api.md`](./admin-management-api.md)）需要一組**業務方法**支撐。生命週期的一半（create/archive/unarchive/delete/restore/login）已由 admin-account-refinement 交付；本文補齊**更新面**與**安全守衛**。

### 目標

- 新增 service 方法：
  - `update`（改顯示名稱 `name`）。
  - `change_password`（**自助**改自己密碼，需驗舊密碼）與 `reset_password`（**super_admin 重設**他人密碼，不需舊密碼）。
  - `set_role`（升降權，改 `admin_role`；承 [`rbac.md`](./rbac.md)）。
  - `list_admins`（委派 repository §4 的欄位謂詞查詢）。
- 疊加**安全不變式**（於 service 層強制，authoritative）：
  - **最後一位可用 SUPER_ADMIN 保護**：不可 archive／delete／降級之，防系統無人可管理。
  - **禁止對自己 archive／delete**：防管理者誤把自己鎖死（改自己權限走 set_role，另有守衛）。
- **密碼變更連帶撤銷該 principal 全部 refresh token**（強制重新登入）。
- 一致的**稽核**：lifecycle 操作寫 `archived_by`／`deleted_by`（成對，已交付）；更新／升降權以結構化 log 記「誰對誰做了什麼」（承 [`admin-management-model.md`](./admin-management-model.md) §2.2）。

### 非目標（Out of scope）

- **HTTP／DTO／授權 dependency**（`require_min_admin_role(SUPER_ADMIN)`）→ [`admin-management-api.md`](./admin-management-api.md)。授權「機制」本身屬 [`rbac.md`](./rbac.md)；本文只**呼叫**其能力並定義業務規則。
- **schema／migration**：本組**無 DDL**（見 [`admin-management-model.md`](./admin-management-model.md) §2.1）。
- **purge**：不採（軟刪除為最終清除，承 admin-account-refinement §2.6）。
- **改 username**：不提供（建立後不可變，見 model §2.3）。
- **User 側**：不動。

---

## 2. 共用元件

### 2.1 `AdminStatusFilter` enum（`app/core/enums.py`，本組新增）

```python
class AdminStatusFilter(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    ALL = "all"
```

供 `list_admins` 與 repository（[`admin-management-model.md`](./admin-management-model.md) §4）、api 查詢參數共用。

### 2.2 時間來源與正規化（沿用既有慣例）

- 時間戳一律 `datetime.now(UTC)`（aware-UTC，與 `AuthService`／`RefreshTokenRepository` 一致，勿用 naive）。
- 密碼 hash 走既有 `hash_password`（argon2id、threadpool）；自助改密碼驗舊密碼走 `verify_password`。
- 密碼強度：沿用 register 的 `min_length=8, max_length=128`（於 DTO 邊界驗，見 api 規格；service 收到的已是合法長度）。

### 2.3 `actor_principal_id`（操作者稽核）

所有「改動他人帳號」的方法都帶 `actor_principal_id: int`（呼叫端＝端點傳入 `current_admin.principal_id`）：

- 狀態轉移（archive/delete）**成對寫入** `archived_by`／`deleted_by`（既有）。
- update/set_role/reset_password 以 log 記 `actor` → `target`（不寫欄，見 model §2.2）。
- 沿用既有 `archive`／`delete` 的 `actor_principal_id: int | None = None` 簽名（seed／script 無操作者傳 `None`）；管理端點必傳。

---

## 3. Service 方法

> 交易邊界：每個變更方法持有**唯一一次 commit**（Unit-of-Work）。失敗 rollback。

### 3.1 既有方法（不重寫，僅補守衛，見 §3.5）

`create` / `get(include_deleted)` / `get_by_username` / `get_by_principal_id` / `archive` / `unarchive` / `restore` / `delete` —— 見 [`admin-account-refinement.md`](./admin-account-refinement.md) §5.3。本文於 `archive` / `delete` 疊加安全不變式（§3.5）。

### 3.2 `update`（更新顯示名稱）

```python
async def update(self, admin_id: int, *, name: str, actor_principal_id: int) -> Admin
```

- `get`（預設過濾軟刪除；軟刪除者不可更新 → `NotFoundError`）。
- 設 `admin.name = name`（`name` 非空、長度 1–100；於 DTO 邊界驗）。**不接受 username**（不可變，model §2.3）。
- commit；`logger.info("admin updated id=%s by=%s field=name", ...)`。
- 回更新後 `Admin`。
- **不撤 token**（改名不影響認證）。

> **升降權不走這裡**：`admin_role` 的變更由 `set_role`（§3.4）獨立處理——它有專屬的安全不變式與稽核語意，混進通用 `update` 會弱化守衛。

### 3.3 密碼變更（兩條路徑，皆撤 refresh token）

**共同語意**：變更密碼後**撤銷該 admin 的全部 refresh token**（`revoke_all_for_principal(principal_id, now)`），強制既有 session 重新登入（access token 殘留 ≤ 一個 TTL，與既有取捨一致）。

#### 3.3.1 `reset_password`（super_admin 重設他人密碼，不需舊密碼）

```python
async def reset_password(self, admin_id: int, *, new_password: str, actor_principal_id: int) -> None
```

- `get`（未刪除）→ `admin.password_hash = await hash_password(new_password)` → `revoke_all_for_principal` → 單一 commit。
- log：`actor` 重設 `target` 密碼（不記明文）。
- 授權（限 super_admin）在 api 層；service 不自判等級。

#### 3.3.2 `change_password`（自助改自己密碼，需驗舊密碼）

```python
async def change_password(
    self, admin_id: int, *, current_password: str, new_password: str
) -> None
```

- `get`（未刪除）→ **驗舊密碼** `verify_password(current_password, admin.password_hash)`，不符 → `UnauthorizedError`（統一訊息，不洩漏細節）。
  - 此路徑帳號必存在（呼叫者＝本人、已通過 `get_current_admin`），故**不需** dummy-verify；直接 `verify_password`。
- `new_password` 不得等於舊密碼（可選守衛；建議擋）→ 不符 `BadRequestError`。
- 設新 hash → `revoke_all_for_principal`（連自己當前 refresh token 一併撤，改密碼後所有裝置重新登入）→ commit。
- **無 `actor_principal_id`**：actor 即 target 本人。

### 3.4 `set_role`（升降權，承 [`rbac.md`](./rbac.md) §5.1）

```python
async def set_role(
    self, admin_id: int, *, admin_role: AdminRole, actor_principal_id: int
) -> Admin
```

- `get`（未刪除）。
- **最後一位 super_admin 守衛**（§3.5）：若這是把「最後一位可用 super_admin」降級（target 現為 super_admin、目標非 super_admin、且 `count_active_super_admins() == 1`）→ `BusinessRuleError`（422，「cannot demote the last super admin」）。
- 設 `admin.admin_role = admin_role.value` → commit → 回更新後 `Admin`。
- log：`actor` 把 `target` 由 `old` 改為 `new`。
- **不撤 token**：授權讀 child 現值（[`rbac.md`](./rbac.md) §5.3）→ 降權對**後端授權即時生效**；`grade` claim 陳舊 ≤ 一個 TTL，由 refresh 自動刷新（[`rbac.md`](./rbac.md) R5）。若要求 UI 即時，前端強制 refresh（api 規格 §7 註明）。
- 授權（限 super_admin 可改他人等級）在 api 層。

> **禁對自己升權**：一個 admin 不應能把自己升成更高等級（提權）。守衛：`actor_principal_id == admin.principal_id` 且目標等級 rank 高於現值 → `ForbiddenError`／`BusinessRuleError`。降自己等級可允許，但若自己是最後一位 super_admin 仍受「最後一位」守衛擋下。細節見 §3.5。

### 3.5 安全不變式（service 層強制，authoritative）

這些不變式**放在 service**（不只 api），確保任何呼叫端（含未來 script）都安全。

1. **最後一位可用 SUPER_ADMIN 保護**——下列操作前，若 target 現為 super_admin 且 `repo.count_active_super_admins() == 1`，則拒絕：
   - `archive(target)` → `BusinessRuleError`（「cannot archive the last super admin」）。
   - `delete(target)` → `BusinessRuleError`（「cannot delete the last super admin」）。
   - `set_role(target, 非 super_admin)` → `BusinessRuleError`（「cannot demote the last super admin」）。
   > 目的：系統永遠至少有一位能管理其他 admin 的 super_admin，杜絕「把唯一管理者停用／降級 → 無人可救」。
2. **禁止對自己 archive／delete**——`actor_principal_id == admin.principal_id` 時：
   - `archive` / `delete` → `BusinessRuleError`（「cannot archive/delete yourself」）。
   > 防誤把自己鎖死；且「離開職務」的正解是由**另一位** super_admin 處理。
3. **禁止自我提權**——`set_role` 中 `actor == target` 且新等級 rank > 現等級 → `ForbiddenError`（「cannot elevate your own role」）。
4. 既有 idempotency 不變：`archive`／`unarchive`／`restore` 對已在目標態者直接回（守衛在**進入實際轉移前**先判 idempotent，再判安全不變式——已封存者再 archive 直接回、不觸發「最後 super_admin」誤擋）。

> **不變式與 idempotency 的順序**：先判「是否已在目標態（idempotent 回）」→ 再判安全不變式 → 再執行轉移。例：對已封存的 super_admin 再 `archive` 應 idempotent 成功（狀態沒變），而非因「最後 super_admin」報錯。

### 3.6 `list_admins`（委派 repository）

```python
async def list_admins(
    self, *, status: AdminStatusFilter = AdminStatusFilter.ACTIVE,
    limit: int = 50, offset: int = 0,
) -> tuple[list[Admin], int]
```

- 委派 `repo.list_admins(status, limit, offset)` + `repo.count_admins(status)`，回 `(rows, total)` 供分頁。
- 純讀取、無 commit。可用性判定不在此（列表本就要顯示各狀態）。

---

## 4. 依賴注入

`AdminService.__init__` 已含 `repo` / `principal_repo` / `refresh_repo`（[`admin-account-refinement.md`](./admin-account-refinement.md) §5.3 交付）。本組新增方法**不需**額外注入——`reset_password`/`change_password` 用既有 `refresh_repo`；`set_role`/`list_admins` 用既有 `repo`。

---

## 5. 流程圖

```
更新名稱：
  update(id, name, actor) ─► get(未刪除) ─► set name ─► commit（不撤 token）

改密碼（自助）：
  change_password(id, cur, new) ─► get ─► verify(cur)✔ ─► new==cur? 擋
       ─► set hash ─► revoke_all_for_principal ─► commit（所有裝置重登）

重設密碼（super_admin）：
  reset_password(id, new, actor) ─► get ─► set hash ─► revoke_all_for_principal ─► commit

升降權：
  set_role(id, role, actor) ─► get ─► [idempotent? 回]
       ─► 守衛：降級最後 super_admin? 自我提權? ─► set admin_role ─► commit（不撤 token；授權讀 child 現值即時）

封存 / 軟刪除（疊加守衛）：
  archive/delete(id, actor) ─► get ─► [idempotent? 回]
       ─► 守衛：對自己? 最後 super_admin? ─► set archived_at/deleted_at + by ─► revoke_all ─► commit
```

---

## 6. 安全性考量

- **最後一位 super_admin**：service 層守衛（§3.5.1）確保系統不被鎖死；即使繞過 api 直呼 service 亦安全。
- **禁自我 archive/delete/提權**（§3.5.2/3.5.3）：限制誤操作與自我提權面。
- **密碼變更撤 token**：改／重設密碼後強制重新登入（撤 refresh token）——被盜帳號經重設密碼後，攻擊者既有 refresh token 立即失效（access token 殘留 ≤ TTL，既有取捨）。
- **改密碼常數性**：自助路徑帳號必存在（已認證本人），直接 `verify_password`；不需 dummy（dummy 是登入端防列舉用）。
- **降權即時性**：授權讀 child 現值 → 降權對後端存取控制即時生效（[`rbac.md`](./rbac.md) §7）；`grade` claim 陳舊由 refresh 刷新。
- **稽核**：狀態轉移寫 `*_by`；更新／升降權／重設密碼以 log 記 actor→target（不記明文密碼）。

---

## 7. TDD 測試計畫（先寫、先看到 RED）

> 由內而外；既有 create/archive/unarchive/delete/restore 測試（[`admin-account-refinement.md`](./admin-account-refinement.md) §8.2）保留，本組新增：

### 7.1 Unit — update
- `update(name=...)`：改到新名稱、`updated_at` 前進；不影響 token。
- 對軟刪除者 `update` → `NotFoundError`。

### 7.2 Unit — 密碼
- `reset_password`：`password_hash` 變更且為 argon2；該 admin 的 refresh token 全撤；不需舊密碼。
- `change_password`：舊密碼正確 → 換新、token 全撤；舊密碼錯 → `UnauthorizedError`、不變更；新密碼等於舊 → `BadRequestError`。

### 7.3 Unit — set_role
- 升 `editor`→`super_admin`（由另一 super_admin 操作）→ 生效；不撤 token。
- 降 `super_admin`→`editor`：非最後一位 → 生效；**最後一位** super_admin 被降 → `BusinessRuleError`。
- 自我提權（actor==target 升等）→ `ForbiddenError`。
- idempotent：set 成相同等級 → 直接回、無副作用。

### 7.4 Unit — 安全不變式
- `archive`／`delete` 最後一位 super_admin → `BusinessRuleError`；尚有其他 active super_admin 時可正常 archive／delete。
- `archive`／`delete` 對自己（actor==target）→ `BusinessRuleError`。
- 已封存的 super_admin 再 `archive` → idempotent 成功（不被「最後 super_admin」誤擋，驗證守衛順序 §3.5）。

### 7.5 Unit — list
- `list_admins(status=ACTIVE/ARCHIVED/DELETED/ALL)` 回對應集合 + 正確 `total`；分頁 `limit/offset` 正確。
- `count_active_super_admins` 邊界（封存／軟刪除／非 super_admin 不計入）。

---

## 8. 實作順序（TDD 里程碑）

0. `AdminStatusFilter` enum + repository `list_admins`／`count_admins`／`count_active_super_admins`（見 [`admin-management-model.md`](./admin-management-model.md) §4、§6）——先落地讀取地基。
1. `update`（改 name）+ 測試（7.1）。
2. `reset_password` / `change_password`（撤 token）+ 測試（7.2）。
3. `set_role` + 最後一位／自我提權守衛 + 測試（7.3）。
4. `archive`／`delete` 疊加安全不變式（最後一位、禁對自己；注意 idempotent 順序）+ 測試（7.4）。
5. `list_admins`（委派 repo）+ 測試（7.5）。
6. 提交前檢查：`ruff` / `ruff format` / `pyright` / `pytest` 全綠。（**無 migration**，見 model §2.1。）

---

## 9. 已定案決策

- ✅ **新增** `update`（僅 name）／`change_password`（自助、驗舊）／`reset_password`（super_admin 重設）／`set_role`（升降權）／`list_admins`；不改 username。
- ✅ **密碼變更撤銷該 principal 全部 refresh token**（強制重登）。
- ✅ **安全不變式放 service 層**：最後一位 super_admin 不可 archive／delete／降級；禁對自己 archive／delete；禁自我提權（§3.5）。
- ✅ **升降權不撤 token**（授權讀 child 現值即時；grade 由 refresh 刷新）；改名不撤 token。
- ✅ **稽核**：狀態轉移寫 `*_by`；更新／升降權／重設密碼走 log（不加 `updated_by` 欄，承 model §2.2）。
- ✅ **無 DDL／migration**（承 model §2.1）。

## 10. 待確認事項（Open Questions）

1. **`change_password` 是否強制新舊不同**：本文建議擋（`BadRequestError`）；若不需可移除該守衛。
2. **降權是否也撤 token**：本文採「不撤」（授權即時、grade 由 refresh 刷新）。若營運要求降權當下連 UI 都即時失效，可改為「降權亦撤 refresh token」——待確認。
3. **升降權稽核表**：承 [`admin-management-model.md`](./admin-management-model.md) §7.1，是否需專用 audit 表記錄等級變更歷史。
