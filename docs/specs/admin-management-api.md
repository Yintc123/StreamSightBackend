# 規格書（API 層）：Admin 管理 — HTTP 端點

> 狀態：**Draft（待實作）** ／ 目標版本：next+2 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「Admin 管理」三份規格的 **API 層**。另兩份：
> - [`admin-management-model.md`](./admin-management-model.md)（資料模型層）
> - [`admin-management-service.md`](./admin-management-service.md)（業務邏輯層）
>
> 🧭 **功能總覽（入口）見 [`admin-management-model.md`](./admin-management-model.md) §0**。
>
> 🔗 交付 [`admin-account-refinement.md`](./admin-account-refinement.md) §1／§11.1 與 [`rbac.md`](./rbac.md) §11.3 延遲的「Admin 管理 API」。**前置依賴**：本組（next+2）依賴 [`rbac.md`](./rbac.md)（next+1）的 `require_min_admin_role`——**rbac 必須先落地**，本文僅**使用**它。
>
> ⚠️ **既有端點（不重寫）**：`POST /admin/auth/login`、`GET /admin/me`（回 `AdminResponse = id/username/name/admin_role`）。本文**新增** `/admin/admins/...` 管理端點與 `/admin/me/password`。

---

## 1. 背景與目標

把 [`admin-management-service.md`](./admin-management-service.md) 的業務能力暴露為 CMS HTTP 端點：**列表 / 明細 / 新增 / 更新名稱 / 升降權 / 封存 / 解除封存 / 軟刪除 / 復原**，加自助 `GET /admin/me`（既有）與 `POST /admin/me/password`（改自己密碼）。**不提供**重設他人密碼端點。

- 定義 request／response schema、**狀態碼**、**授權矩陣**。
- 端點傳 `actor_principal_id = current_admin.principal_id` 供稽核。
- **業務不變式由 service 強制**（受保護 root、super_admin 須先降級、禁對自己／自我提權）；api 僅轉呼叫，例外經全域 handler 映射狀態碼。

### 非目標
- 業務規則實作、授權 dependency 實作（[`rbac.md`](./rbac.md)）、schema／migration（見 model §6）、transfer ownership、purge、改 username。

---

## 2. 授權模型

- **管理他人 admin**（`/admin/admins/...` 全部）：`Depends(require_min_admin_role(AdminRole.SUPER_ADMIN))`（[`rbac.md`](./rbac.md) §5.4）。等級不足 → **403**。
- **自助**（`/admin/me`、`/admin/me/password`）：`Depends(get_current_admin)`（任何已認證 active admin）。
- 未認證／token 失效／帳號不可用 → **401**；非 admin 角色 → **403**。
- **授權讀 child 現值**（非盲信 `grade` claim）→ 降權即時（[`rbac.md`](./rbac.md) §5.3）。

> **403 vs 422 的分工（M2）**：**403** 專屬「授權層」失敗（等級不足、非 admin 角色）；**業務規則違反**（受保護 root、super_admin 須先降級、禁對自己、自我提權）一律 **422 `BusinessRuleError`**。

---

## 3. 端點總覽

前綴 `/admin`（掛既有 `app/api/routers/admin/router.py`）。

| Method & Path | 用途 | 授權 | 成功碼 | Service |
|---|---|---|---|---|
| `POST /admin/auth/login` | 登入（既有） | 公開 | 200 | `admin_login` |
| `GET /admin/me` | 讀自身（既有） | 已認證 admin | 200 | — |
| `POST /admin/me/password` | 改自己密碼 | 已認證 admin | 204 | `change_password` |
| `GET /admin/admins` | 列表（篩狀態、分頁） | SUPER_ADMIN | 200 | `list_admins` |
| `POST /admin/admins` | 新增（恆 `is_protected=False`） | SUPER_ADMIN | 201 | `create` |
| `GET /admin/admins/{id}` | 明細 | SUPER_ADMIN | 200 | `get(include_deleted=True)` |
| `PATCH /admin/admins/{id}` | 更新顯示名稱 | SUPER_ADMIN | 200 | `update` |
| `PUT /admin/admins/{id}/role` | 升降權 | SUPER_ADMIN | 200 | `set_admin_role` |
| `POST /admin/admins/{id}/archive` | 封存 | SUPER_ADMIN | 200 | `archive` |
| `POST /admin/admins/{id}/unarchive` | 解除封存 | SUPER_ADMIN | 200 | `unarchive` |
| `DELETE /admin/admins/{id}` | 軟刪除 | SUPER_ADMIN | **200** | `delete` |
| `POST /admin/admins/{id}/restore` | 復原軟刪除 | SUPER_ADMIN | 200 | `restore` |

> **設計取捨**：
> - 生命週期轉移用 `POST /{id}/{action}`（archive/unarchive/restore，語意明確、天然 idempotent）；軟刪除用 `DELETE /{id}`（實為軟刪除）。
> - **升降權獨立 `PUT /{id}/role`**（呼叫 `set_admin_role`）——它有專屬守衛與稽核，獨立資源更清楚。
> - **移除 super_admin 需兩步**：先 `PUT /{id}/role` 降為 editor/viewer，再 `POST /{id}/archive` 或 `DELETE /{id}`（直接對 super_admin archive/delete → 422，見 §6）。
> - **軟刪除回 200 + `AdminSummary`**（非 204）：軟刪除是**狀態轉移**（資源仍在、只是設 `deleted_at`），回更新後資源比 204「無內容」語意更正確，且與 archive/unarchive/restore 一致（L2 修正）。
> - **無 transfer-ownership 端點**、**不開放切換 `is_protected`**、**不提供重設他人密碼端點**（承 model §2.2、service §3.3）。

---

## 4. DTO / Schema（`app/api/routers/admin/schemas.py`）

### 4.1 既有（不改）
- `AdminResponse`（`id/username/name/admin_role`）——`/admin/me`、以及新增／更新／升降權的回應（操作 active admin）。

### 4.2 新增

```python
# ── requests ──
class AdminCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    admin_role: AdminRole = AdminRole.VIEWER          # fail-safe；is_protected 不對外（恆 False）

    @field_validator("username")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_username(v)

class AdminUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)   # 只改 name（username 不可變、role 走 /role）

class AdminRoleUpdateRequest(BaseModel):
    admin_role: AdminRole

class ChangeOwnPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)

# ── responses ──
class AdminSummary(BaseModel):
    """管理列表／明細用：含狀態、稽核與 is_protected。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    name: str
    admin_role: AdminRole
    is_protected: bool                 # 前端據此標示「root、不可移除」並禁用相關按鈕
    is_active: bool                    # 計算屬性（from_attributes 讀取）
    archived_at: datetime | None
    archived_by: int | None            # 操作者 principal_id（穩定參照）
    archived_by_username: str | None   # 操作者 username（顯示用；repo 自 join 解析，L1）
    deleted_at: datetime | None
    deleted_by: int | None
    deleted_by_username: str | None    # 同上
    created_at: datetime
    updated_at: datetime

class AdminListResponse(BaseModel):
    items: list[AdminSummary]
    total: int
    limit: int
    offset: int
```

> **`AdminResponse` vs `AdminSummary`**：`AdminResponse`（精簡）用於「操作單一 active admin」的回身；`AdminSummary`（含狀態／`is_protected`）用於**列表與明細**，讓 UI 呈現封存／刪除狀態與「root 不可移除」。`is_protected` 只出現在 `AdminSummary`（供 UI 判斷可否操作），`AdminResponse` 不含。

### 4.3 查詢參數
`GET /admin/admins?status=active|archived|deleted|all&limit=&offset=`——`status` 為 `AdminStatusFilter`（預設 `active`）；`limit` 預設 50、上限 200；`offset ≥ 0`。

---

## 5. 端點細節（重點）

- `POST /admin/me/password`：`change_password(current_admin.id, ...)` → **204**（token 已撤，需重登）；舊密碼錯 → **401**；新==舊 → **400**。
- `GET /admin/admins`：`list_admins(status, limit, offset)` → **200** `AdminListResponse`。
- `POST /admin/admins`：`create(..., is_protected=False)` → **201** `AdminResponse`；格式不符 → **400**；username 重複 → **409**。
- `GET /admin/admins/{id}`：`get_row(include_deleted=True)` → **200** `AdminSummary`；不存在 → **404**。（**用 `get_row` 非 `get`**：`AdminSummary` 含 `archived_by_username` / `deleted_by_username`，需 JOIN 解析，裸 `Admin` 產不出——見 [`admin-management-service.md`](./admin-management-service.md) §3.9。）
- `PATCH /admin/admins/{id}`：`update(id, name=..., actor=...)` → **200** `AdminResponse`；軟刪除者 → **404**。
- `PUT /admin/admins/{id}/role`：`set_admin_role(id, admin_role=..., actor=...)` → **200** `AdminResponse`；降級受保護 root → **422**；自我提權 → **422**；軟刪除者 → **404**。
- `POST /{id}/archive`｜`POST /{id}/unarchive`｜`POST /{id}/restore`｜`DELETE /{id}` → **200** `AdminSummary`（四個生命週期端點皆回更新後資源，含新狀態；archive/unarchive/restore idempotent）。
  - 對 super_admin（未降級）archive／delete → **422**；受保護 root archive／delete → **422**；對自己 archive／delete → **422**；不存在／已軟刪除（archive/unarchive/delete）→ **404**。

> **四個生命週期端點統一回 `200 + AdminSummary`**（L2）：軟刪除是狀態轉移、資源仍在，回更新後資源比 204 語意更正確、前端也立即見到新狀態。轉移完成後以 `get_row(include_deleted=True)` 取帶 username 的單列（service §3.9）建 `AdminSummary`——`delete` 亦然（`delete` 本身回 `None`，回身另取 row）。

---

## 6. 狀態碼與例外映射（全域 handler）

| 情境 | 例外 | HTTP |
|---|---|---|
| 未認證 / token 失效 / 帳號不可用 / 改密碼舊密碼錯 | `UnauthorizedError` | 401 |
| 等級不足 / 非 admin 角色 | `ForbiddenError` | 403 |
| admin 不存在 / 已軟刪除（一般讀寫） | `NotFoundError` | 404 |
| username 已存在 | `ConflictError` | 409 |
| username 格式不符 / 新舊密碼相同 | `BadRequestError` | 400 |
| DTO 欄位驗證（長度等） | pydantic | 422 |
| **受保護 root 被降級/封存/刪除** ／ **super_admin 未降級即封存/刪除** ／ **對自己 archive/delete** ／ **自我提權** | `BusinessRuleError` | **422** |

> **M2 一致性**：所有「業務規則違反」統一 `BusinessRuleError`（422）；403 只給授權層（等級不足、角色不符）。
>
> **DB CHECK 不影響 API 映射（A）**：model §2.3 的 `ck_admins_protected_is_super`／`ck_admins_protected_is_active` 是**繞過 service 才會觸發的結構兜底**；正常 API 流程一律先被 service 受保護守衛擋成 **422**，`IntegrityError` **不會由 API 浮現**，故本表無需新增對應列。

---

## 7. 前端契約與即時性

- **升降權即時性**：後端授權讀 child 現值 → 降權即時；被改 admin 手上既有 access token 的 `grade` 陳舊 ≤ 一個 TTL。前端在「被改的是自己」時強制 refresh 一次（或重打 `/me`）刷新 UI（[`rbac.md`](./rbac.md) §5.5）。
- **自助改密碼**：成功後該 admin 全部 refresh token 已撤；本人需重新登入。（無 super_admin 重設他人密碼；忘記密碼的復原見 [`admin-management-service.md`](./admin-management-service.md) §6。）
- **列表**：`AdminSummary.is_protected`／`is_active`／`archived_at`／`deleted_at` 供 UI 標示「root 不可移除／停用／已刪」並決定顯示哪些動作（例：對 super_admin 只顯示「先降級」而非直接封存）。

---

## 8. TDD 測試計畫（Integration，`tests/integration/test_admin_management_api.py`）

> 沿用 `admin` fixture（super_admin）。需要 viewer/editor 以 `AdminService.create(..., admin_role=...)` 佈局；需要受保護 root 以 `create(..., is_protected=True)`。

### 8.1 授權
- viewer/editor 打任一 `/admin/admins/...` → **403**；super_admin → 通過。user token → **403**；無 token → **401**。

### 8.2 新增 / 列表 / 明細
- `POST /admin/admins` 建 viewer → **201** `AdminResponse`（無狀態欄、無 `is_protected`）；建出的 admin `is_protected=False`。
- 重複 username → **409**；格式（`a@b`）→ **400**；過短密碼／缺欄 → **422**。
- `GET /admin/admins?status=active|archived|deleted|all` 回對應集合 + `total`；分頁正確；`AdminSummary` 含 `is_protected`／`is_active`／時間戳。
- `GET /admin/admins/{id}`（含軟刪除者）→ **200** `AdminSummary`。

### 8.3 更新 / 升降權 / 密碼
- `PATCH /{id}` 改 name → **200**。
- `PUT /{id}/role` 升 editor→super_admin → **200**；降**受保護 root** → **422**；降非受保護 super_admin → **200**。
- `POST /me/password` 舊對 → **204**（舊 refresh 失效、新密碼可登入）；舊錯 → **401**；新==舊 → **400**。
- **無 `POST /{id}/password`**（重設他人密碼）端點——請求該路徑應 404（未定義）。

### 8.4 生命週期 + 守衛
- 直接 `POST /{id}/archive` 或 `DELETE /{id}` 一個 **super_admin** → **422**（先降級）；受保護 root → **422**；對自己 → **422**。
- 兩步移除：`PUT /{id}/role`（super_admin→viewer）→ `DELETE /{id}` → **200** `AdminSummary`（`deleted_at` 有值）；`status=deleted` 可見；`POST /{id}/restore` → **200** 後可再登入。
- `AdminSummary` 的 `archived_by_username`／`deleted_by_username` 反映操作者（actor）username（L1）。
- `POST /{id}/archive`（對已降級者）→ **200**，該 admin 登入 → **401**、既發 refresh → **401**；再 archive → **200**（idempotent）；`unarchive` → **200** 可再登入。

### 8.5 回應形狀
- create/update/set_admin_role 回 `AdminResponse`（無狀態欄）；list/明細/archive/unarchive/restore 回 `AdminSummary`（含 `is_protected`／`is_active`／時間戳）。

---

## 9. 實作順序（TDD 里程碑）

> 前置：service 方法（[`admin-management-service.md`](./admin-management-service.md)）與 rbac 的 `require_min_admin_role` 已就緒。

1. DTO（§4.2；含 `AdminSummary.is_protected`）。
2. 自助 `POST /admin/me/password`（8.3）。
3. 列表／新增／明細（8.2）。
4. 更新／升降權（`PUT /{id}/role` → `set_admin_role`）（8.3）。
5. 生命週期 `POST /{id}/archive|unarchive|restore`、`DELETE /{id}` + 守衛（8.4）。
6. 授權守衛 `require_min_admin_role(SUPER_ADMIN)` + 授權測試（8.1）。
7. 提交前檢查全綠。

---

## 10. 已定案決策

- ✅ `/admin/admins/...` 限 SUPER_ADMIN；`/admin/me*` 自助限已認證 admin。
- ✅ 生命週期用 `POST /{id}/{action}`、軟刪除 `DELETE /{id}`；**四個生命週期端點統一回 200 + `AdminSummary`**（L2，軟刪除是狀態轉移非移除）；升降權獨立 `PUT /{id}/role`（`set_admin_role`）；改自己密碼 `POST /me/password`（需舊）。**不提供重設他人密碼端點**。
- ✅ **移除 super_admin 兩步**（先降級再封存/刪除）；直接對 super_admin/受保護 root/自己 archive-delete、自我提權 → **422**。
- ✅ 兩種回應 DTO：`AdminResponse`（精簡）／`AdminSummary`（含 `is_protected`／狀態；且含 `archived_by_username`／`deleted_by_username`，由 repo 自 join 解析操作者名稱，L1）。
- ✅ 三份規格單一入口＝[`admin-management-model.md`](./admin-management-model.md) §0（L3）。
- ✅ 業務不變式由 service 強制；api 轉呼叫、全域 handler 映射（**M2**：業務違反統一 422，403 只給授權）。
- ✅ 端點傳 `actor_principal_id`；建立恆 `is_protected=False`（不開放經 API 建受保護 admin）。

## 11. 待確認事項（Open Questions）

1. **列表分頁**預設 50 / 上限 200（本文採）；是否需 cursor 分頁（admin 量少，offset 應足夠）。
2. ~~**登入 rate-limit / lockout**~~ **暫不考慮**：由基礎設施層（反向代理 / WAF）對 `/admin/auth/login` 限流即可。
3. **降權即時性**：若要求降權當下前端即時失效，改「降權亦撤 token」（見 [`admin-management-service.md`](./admin-management-service.md) §10.2）。
4. **transfer-ownership 端點**：本組不提供（model §2.2）。
5. **重設他人密碼端點**：**目前不規劃**（承 service §3.3／§10 Q5）；忘記密碼復原走「軟刪除後重建」。若痛點浮現再議（含 step-up）。
