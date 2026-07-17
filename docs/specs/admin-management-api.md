# 規格書（API 層）：Admin 管理 — HTTP 端點

> 狀態：**Draft（待實作）** ／ 目標版本：next+2 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「Admin 管理」三份規格的 **API 層**（HTTP 端點／DTO／授權／狀態碼）。另兩份：
> - [`admin-management-model.md`](./admin-management-model.md)（資料模型層）
> - [`admin-management-service.md`](./admin-management-service.md)（業務邏輯層）
>
> 🔗 交付 [`admin-account-refinement.md`](./admin-account-refinement.md) §1／§11.1 與 [`rbac.md`](./rbac.md) §11.3 延遲的「Admin 管理 API」。**授權機制**（`require_min_admin_role`、`grade` claim）本身屬 [`rbac.md`](./rbac.md)——本文**使用**它做端點守衛。
>
> ⚠️ **既有端點（不重寫）**：`POST /admin/auth/login`（username + password → role=1 token；[`admin-account-refinement.md`](./admin-account-refinement.md) §5.6 交付）、`GET /admin/me`（回 `AdminResponse = id/username/name/admin_role`）。本文**新增**一組 `/admin/admins/...` 管理端點與 `/admin/me/password` 自助改密碼。

---

## 1. 背景與目標

把 [`admin-management-service.md`](./admin-management-service.md) 的業務能力**暴露為 CMS HTTP 端點**，供後台管理介面對 admin 帳號做 CRUD 與生命週期操作。

### 目標

- 定義 `/admin/admins/...` 管理端點：**列表 / 明細 / 新增 / 更新名稱 / 升降權 / 重設密碼 / 封存 / 解除封存 / 軟刪除 / 復原**。
- 定義自助端點：`GET /admin/me`（既有）、`POST /admin/me/password`（改自己密碼）。
- 定義 request／response schema（DTO）與**狀態碼**、**授權矩陣**。
- 端點把 `current_admin.principal_id` 作為 `actor_principal_id` 傳入 service（稽核）。

### 非目標（Out of scope）

- **業務規則／不變式**（最後一位 super_admin、禁對自己）→ 由 service 強制（[`admin-management-service.md`](./admin-management-service.md) §3.5）；本層只轉呼叫並讓例外經全域 handler 映射狀態碼。
- **授權 dependency 的實作**（`require_min_admin_role`）→ [`rbac.md`](./rbac.md) §5.3；本文假設其已存在並使用。
- **schema／migration**：無（見 [`admin-management-model.md`](./admin-management-model.md) §2.1）。

---

## 2. 授權模型

- **管理他人 admin**（`/admin/admins/...` 全部）：`Depends(require_min_admin_role(AdminRole.SUPER_ADMIN))`（[`rbac.md`](./rbac.md) §5.4）。等級不足 → **403**。
- **自助**（`/admin/me`、`/admin/me/password`）：`Depends(get_current_admin)`（任何已認證 active admin）。
- 未認證／token 失效 → **401**（`get_current_admin` / oauth2 bearer）。
- 非 admin 角色（user token 打 admin 端點）→ **403**（既有 `get_admin_from_token`）。
- 封存／軟刪除的 admin 其 token 已因 `is_active=False` 被 `get_current_admin` 擋下 → **401**。

> **授權讀 child 現值**：`require_min_admin_role` 讀 `admins.admin_role` 當前值（非盲信 `grade` claim），故降權即時生效（[`rbac.md`](./rbac.md) §5.3／§7）。

---

## 3. 端點總覽

前綴 `/admin`，掛在既有 `app/api/routers/admin/router.py`（tags=["admin"]）。

| Method & Path | 用途 | 授權 | 成功碼 | Service |
|---|---|---|---|---|
| `POST /admin/auth/login` | 登入（既有） | 公開 | 200 | `AuthService.admin_login` |
| `GET /admin/me` | 讀自身（既有） | 已認證 admin | 200 | — |
| `POST /admin/me/password` | 改自己密碼 | 已認證 admin | 204 | `change_password` |
| `GET /admin/admins` | 列表（可篩狀態、分頁） | SUPER_ADMIN | 200 | `list_admins` |
| `POST /admin/admins` | 新增 admin | SUPER_ADMIN | 201 | `create` |
| `GET /admin/admins/{id}` | 明細 | SUPER_ADMIN | 200 | `get(include_deleted=True)` |
| `PATCH /admin/admins/{id}` | 更新顯示名稱 | SUPER_ADMIN | 200 | `update` |
| `PUT /admin/admins/{id}/role` | 升降權 | SUPER_ADMIN | 200 | `set_role` |
| `POST /admin/admins/{id}/password` | 重設他人密碼 | SUPER_ADMIN | 204 | `reset_password` |
| `POST /admin/admins/{id}/archive` | 封存 | SUPER_ADMIN | 200 | `archive` |
| `POST /admin/admins/{id}/unarchive` | 解除封存 | SUPER_ADMIN | 200 | `unarchive` |
| `DELETE /admin/admins/{id}` | 軟刪除 | SUPER_ADMIN | 204 | `delete` |
| `POST /admin/admins/{id}/restore` | 復原軟刪除 | SUPER_ADMIN | 200 | `restore` |

> **設計取捨**：
> - 生命週期轉移用 **`POST /{id}/{action}`**（archive/unarchive/restore）而非 PATCH 狀態欄——動作語意明確、天然 idempotent、不需客戶端拼狀態。
> - **升降權獨立為 `PUT /{id}/role`**（非併入 `PATCH /{id}`）：升降權有專屬安全不變式與稽核語意，獨立資源使授權與稽核更清楚（承 [`admin-management-service.md`](./admin-management-service.md) §3.2 的分離原則）。
> - **軟刪除用 `DELETE`**（RESTful 慣例），實際是軟刪除（設 `deleted_at`），非物理刪除。
> - `GET /{id}` 用 `include_deleted=True`：管理者需能查已軟刪除者以決定是否 restore。

---

## 4. DTO / Schema（`app/api/routers/admin/schemas.py`）

### 4.1 既有（不改）

- `AdminResponse`（`id/username/name/admin_role`）——`/admin/me`、以及**新增／更新／升降權**的回應（active admin 的公開視圖）。

### 4.2 新增

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime
from app.core.enums import AdminRole
from app.core.security import normalize_username

# ── requests ──
class AdminCreateRequest(BaseModel):
    """POST /admin/admins。username 於邊界正規化；格式硬驗在 service（BadRequestError）。"""
    username: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    admin_role: AdminRole = AdminRole.VIEWER  # fail-safe 預設最低權限

    @field_validator("username")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_username(v)

class AdminUpdateRequest(BaseModel):
    """PATCH /admin/admins/{id}。只允許改 name（username 不可變、admin_role 走 /role）。"""
    name: str = Field(min_length=1, max_length=100)

class AdminRoleUpdateRequest(BaseModel):
    """PUT /admin/admins/{id}/role。"""
    admin_role: AdminRole

class AdminPasswordResetRequest(BaseModel):
    """POST /admin/admins/{id}/password（super_admin 重設他人，不需舊密碼）。"""
    new_password: str = Field(min_length=8, max_length=128)

class ChangeOwnPasswordRequest(BaseModel):
    """POST /admin/me/password（自助，需舊密碼）。"""
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)

# ── responses ──
class AdminSummary(BaseModel):
    """管理列表／明細用：含狀態與稽核欄（AdminResponse 之外多出封存／刪除資訊）。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    name: str
    admin_role: AdminRole
    is_active: bool                    # 計算屬性，pydantic 由 from_attributes 讀取
    archived_at: datetime | None
    archived_by: int | None
    deleted_at: datetime | None
    deleted_by: int | None
    created_at: datetime
    updated_at: datetime

class AdminListResponse(BaseModel):
    items: list[AdminSummary]
    total: int
    limit: int
    offset: int
```

> **`AdminResponse` vs `AdminSummary`（purposeful DTO）**：
> - `AdminResponse`（精簡：`id/username/name/admin_role`）用於「操作單一 active admin」的回應（登入者 `/me`、create/update/set_role 後回身）——這些情境該 admin 恆為 active，狀態欄無意義。
> - `AdminSummary`（含狀態／稽核）用於**管理列表與明細**——需顯示封存／刪除狀態供管理決策。此即 [`admin-account-refinement.md`](./admin-account-refinement.md) §5.1 預告的 `AdminSummary`。

### 4.3 `AdminStatusFilter`（查詢參數 enum）

`GET /admin/admins?status=active|archived|deleted|all&limit=&offset=`——`status` 型別為 [`admin-management-service.md`](./admin-management-service.md) §2.1 的 `AdminStatusFilter`（預設 `active`）。`limit` 預設 50、上限 200；`offset` ≥ 0。

---

## 5. 端點細節

### 5.1 `POST /admin/me/password`（自助改密碼）
- Body `ChangeOwnPasswordRequest`。
- 呼叫 `change_password(current_admin.id, current_password=..., new_password=...)`。
- 成功 → **204**（改密碼後該 admin 全部 refresh token 已撤，前端需以新密碼重新登入）。
- 舊密碼錯 → **401**；新舊相同 → **400**（`BadRequestError`）。

### 5.2 `GET /admin/admins`（列表）
- Query：`status`（`AdminStatusFilter`，預設 `active`）、`limit`、`offset`。
- 呼叫 `list_admins(status=..., limit=..., offset=...)` → `(rows, total)`。
- 回 `AdminListResponse`（`items` 為 `AdminSummary`）→ **200**。

### 5.3 `POST /admin/admins`（新增）
- Body `AdminCreateRequest`。
- 呼叫 `create(username=..., name=..., password=..., admin_role=...)`。
- 成功 → **201** `AdminResponse`。
- username 格式不符 → **400**（service `BadRequestError`）；username 已存在 → **409**（`ConflictError`）。

### 5.4 `GET /admin/admins/{id}`（明細）
- 呼叫 `get(id, include_deleted=True)` → **200** `AdminSummary`。
- 不存在 → **404**。

### 5.5 `PATCH /admin/admins/{id}`（更新名稱）
- Body `AdminUpdateRequest`。呼叫 `update(id, name=..., actor_principal_id=current_admin.principal_id)`。
- 成功 → **200** `AdminResponse`。軟刪除者 → **404**。

### 5.6 `PUT /admin/admins/{id}/role`（升降權）
- Body `AdminRoleUpdateRequest`。呼叫 `set_role(id, admin_role=..., actor_principal_id=current_admin.principal_id)`。
- 成功 → **200** `AdminResponse`（含新 `admin_role`）。
- 降級最後一位 super_admin → **422**（`BusinessRuleError`）；自我提權 → **403**（`ForbiddenError`）；軟刪除者 → **404**。

### 5.7 `POST /admin/admins/{id}/password`（重設他人密碼）
- Body `AdminPasswordResetRequest`。呼叫 `reset_password(id, new_password=..., actor_principal_id=...)`。
- 成功 → **204**（target 全部 refresh token 已撤，target 需重新登入）。

### 5.8 生命週期：archive / unarchive / delete / restore
- `POST /{id}/archive` → `archive(id, actor_principal_id=...)` → **200** `AdminSummary`（回身含新狀態）。idempotent。
- `POST /{id}/unarchive` → `unarchive(id)` → **200** `AdminSummary`。idempotent。
- `DELETE /{id}` → `delete(id, actor_principal_id=...)` → **204**（軟刪除）。
- `POST /{id}/restore` → `restore(id)` → **200** `AdminSummary`。idempotent。
- 守衛違反（對自己 archive/delete、最後一位 super_admin）→ **422**（`BusinessRuleError`）；不存在／已軟刪除（對 archive/unarchive/delete）→ **404**。

> archive/unarchive/restore 回 `AdminSummary`（讓前端立即看到 `is_active`／時間戳更新）；delete 回 **204**（軟刪除無回身內容需求，狀態可由後續 `GET` 取得）。

---

## 6. 狀態碼與例外映射（沿用全域 handler）

不在 router 內散落 try/except；service 拋自訂例外，由全域 handler 統一回帶 `request_id` 的 JSON（承專案慣例）。

| 情境 | 例外 | HTTP |
|---|---|---|
| 未認證 / token 失效 / 帳號不可用 | `UnauthorizedError` | 401 |
| 等級不足 / 非 admin 角色 / 自我提權 | `ForbiddenError` | 403 |
| admin 不存在 / 已軟刪除（一般讀寫） | `NotFoundError` | 404 |
| username 已存在 | `ConflictError` | 409 |
| username 格式不符 / 新舊密碼相同 | `BadRequestError` | 400 |
| DTO 欄位驗證（長度等） | pydantic 422 | 422 |
| 最後一位 super_admin / 對自己 archive-delete | `BusinessRuleError` | 422 |

---

## 7. 前端契約與即時性

- **升降權即時性**：後端授權讀 child 現值 → 降權對存取控制即時；但被改 admin 手上既有 access token 的 `grade` claim 會陳舊 ≤ 一個 TTL。前端在「被改的是自己」時應**強制 refresh 一次**（或重打 `/me`）以刷新 UI（[`rbac.md`](./rbac.md) §5.5）。
- **改／重設密碼**：成功後該 admin 全部 refresh token 已撤；前端（若為本人）需導向重新登入。
- **列表狀態**：`AdminSummary.is_active` / `archived_at` / `deleted_at` 供 UI 標示「停用／已刪」並提供 unarchive／restore 動作。

---

## 8. TDD 測試計畫（Integration，`tests/integration/test_admin_management_api.py`；先寫、先看到 RED）

> 沿用既有 `admin` fixture（seed-equivalent，`admin_role=super_admin`）。需要 viewer/editor 者以 `AdminService.create(..., admin_role=...)` 佈局。

### 8.1 授權
- viewer/editor admin 打任一 `/admin/admins/...` → **403**；super_admin → 通過。
- user token 打 `/admin/admins` → **403**；無 token → **401**。

### 8.2 新增 / 列表 / 明細
- `POST /admin/admins` 建 viewer → **201**，body 為 `AdminResponse`（`admin_role="viewer"`、無狀態欄）。
- 重複 username → **409**；格式不符（`a@b`）→ **400**；缺欄／過短密碼 → **422**。
- `GET /admin/admins?status=active` 回 active 集合 + `total`；分頁 `limit/offset` 正確。
- 封存一位後 `status=archived` 出現、`status=active` 不出現；軟刪除後 `status=deleted` 出現。
- `GET /admin/admins/{id}`（含軟刪除者，`include_deleted`）→ **200** `AdminSummary`。

### 8.3 更新 / 升降權 / 密碼
- `PATCH /admin/admins/{id}` 改 name → **200**，name 更新。
- `PUT /admin/admins/{id}/role` 升 editor→super_admin → **200**；降唯一 super_admin → **422**。
- `POST /admin/admins/{id}/password` 重設 → **204**，之後 target 舊 refresh token 走 `/auth/refresh` → **401**。
- `POST /admin/me/password` 舊密碼正確 → **204**，舊 refresh token 失效、新密碼可登入；舊密碼錯 → **401**；新舊相同 → **400**。

### 8.4 生命週期端點
- `POST /{id}/archive` → **200**，之後該 admin 登入 → **401**、既發 refresh → **401**；再次 archive → **200**（idempotent）。
- `POST /{id}/unarchive` → **200**，可再登入。
- `DELETE /{id}` → **204**，`GET /{id}` 仍可見（軟刪除）、以 `status=deleted` 列出；`POST /{id}/restore` → **200** 後可再登入。
- 對自己 `archive`／`DELETE` → **422**；archive/delete 最後一位 super_admin → **422**。

### 8.5 回應形狀
- create/update/set_role 回 `AdminResponse`（無狀態欄）；list/明細/archive/unarchive/restore 回 `AdminSummary`（含 `is_active`／`archived_at`／`deleted_at`）。

---

## 9. 實作順序（TDD 里程碑）

> 前置：[`admin-management-service.md`](./admin-management-service.md) 的 service 方法與 [`rbac.md`](./rbac.md) 的 `require_min_admin_role` 已就緒。

1. DTO：`AdminCreateRequest`／`AdminUpdateRequest`／`AdminRoleUpdateRequest`／`AdminPasswordResetRequest`／`ChangeOwnPasswordRequest`／`AdminSummary`／`AdminListResponse`（8.5）。
2. 自助 `POST /admin/me/password`（8.3）。
3. 列表／新增／明細 `GET|POST /admin/admins`、`GET /admin/admins/{id}`（8.2）。
4. 更新／升降權／重設密碼 `PATCH /{id}`、`PUT /{id}/role`、`POST /{id}/password`（8.3）。
5. 生命週期 `POST /{id}/archive|unarchive|restore`、`DELETE /{id}`（8.4）。
6. 授權守衛（`require_min_admin_role(SUPER_ADMIN)` 掛各端點）+ 授權測試（8.1）。
7. 提交前檢查：`ruff` / `ruff format` / `pyright` / `pytest` 全綠。（**無 migration**。）

---

## 10. 已定案決策

- ✅ **`/admin/admins/...` 全部限 SUPER_ADMIN**；`/admin/me*` 自助限已認證 admin（§2）。
- ✅ 生命週期用 **`POST /{id}/{action}`**（archive/unarchive/restore）、軟刪除用 **`DELETE /{id}`**（§3）。
- ✅ **升降權獨立 `PUT /{id}/role`**、**改自己密碼 `POST /me/password`（需舊密碼）**、**重設他人密碼 `POST /{id}/password`（不需舊密碼）**。
- ✅ **兩種回應 DTO**：`AdminResponse`（精簡，操作 active admin）／`AdminSummary`（含狀態，列表與明細）。
- ✅ **業務不變式由 service 強制**、api 只轉呼叫並由全域 handler 映射狀態碼（最後 super_admin／禁對自己 → 422、自我提權 → 403）。
- ✅ **端點傳 `actor_principal_id = current_admin.principal_id`** 供稽核。
- ✅ **無 schema／migration**（承 model §2.1）。

## 11. 待確認事項（Open Questions）

1. **列表預設 `limit`／上限**（本文採 default 50 / max 200）與是否需 cursor 分頁（admin 量少，offset 分頁應足夠）。
2. **admin 登入 rate-limit / lockout**：承 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md) §11／[`admin-account-refinement.md`](./admin-account-refinement.md) §11.4 既有缺口，`/admin/auth/login` 與 `/admin/me/password` 是否加 Redis 限流——另議。
3. **`grade` claim 是否隨升降權即時性需求調整**（若要求降權當下前端即時失效，改為降權亦撤 token；見 [`admin-management-service.md`](./admin-management-service.md) §10.2）。
