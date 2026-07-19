# 規格書（API 層）：資料記錄（Record）管理 — HTTP／DTO／授權

> 狀態：**草案（尚未實作）** ／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「資料記錄管理」的 **API 層**（HTTP 端點／DTO／授權接線）。資料表（欄位／FK／索引／migration）見 [`records-model.md`](./records-model.md)；service／業務規則（grade 寫入守衛、交易邊界、匯入逐列驗證）見 `records-service.md`（§0 非目標）。初始 admin 為真實 DB 列（[`bootstrap-hidden-admin.md`](./bootstrap-hidden-admin.md））→ 無 bootstrap 寫入特例。
>
> 🔗 **契約來源**：本 API 是前端 [StreamSightStreamlit `data-source.md`](../../../StreamSightStreamlit/docs/specs/data-source.md) 之 `DataSource` Protocol 的**真實 REST 後端**，並直接服務頁面 [`03-data-management.md`](../../../StreamSightStreamlit/docs/specs/pages/03-data-management.md)。上線後前端只把 `MockDataSource` 換成 `ApiDataSource`，**頁面與行為測試零改動**——本規格的每個回應形狀都以此為最高約束。

---

## 0. 功能總覽（先讀這裡）

**一句話**：把前端 `DataSource` 的六個方法（`list_records` / `get_record` / `create_record` / `update_record` / `delete_record` / `bulk_create`）落成 `/records*` REST 端點，讀取全 admin 可用（viewer+）、寫入限 editor+，並額外提供分類下拉來源端點。

**契約速查（前端 `DataSource` 方法 → 端點 → 回應型別）**：

| 前端方法 | 端點 | 授權 | 成功碼 | 回應（前端型別） |
|---|---|---|---|---|
| `list_records(page,size,category,keyword,sort,include_deleted)` | `GET /records` | viewer+ | 200 | `Page{items,total,page,size}` |
| `get_record(id)` | `GET /records/{id}` | viewer+ | 200 | `Record` |
| `create_record(data,actor)` | `POST /records` | editor+ | 201 | `Record` |
| `update_record(id,data,actor)` | `PATCH /records/{id}` | editor+ | 200 | `Record` |
| `delete_record(id,actor)` | `DELETE /records/{id}` | editor+ | 204 | `None` |
| `bulk_create(rows,actor)` | `POST /records/bulk` | editor+ | 200 | `ImportResult{created,errors}` |
| （下拉來源，非 DataSource 方法） | `GET /records/categories` | viewer+ | 200 | `[Category]` |

> **可編輯欄位（前端只准填這四個）**：`title / value / category / note`。`id / created_by / created_at / updated_at / deleted_at` 一律由來源端管理，請求體含這些欄位一律忽略（不報錯，對齊前端「前端不得指定」，§4.2）。

**例外 → HTTP（沿用前端 `data-source.md` §例外，落到後端既有例外體系）**：

| 前端例外 | 後端例外（繼承既有基類，`records-model.md` §0） | 觸發 | HTTP |
|---|---|---|---|
| `RecordNotFound` | `RecordNotFoundError(NotFoundError)` | get/update/delete 遇不存在或已軟刪除 id | 404 |
| `PermissionDenied` | `ForbiddenError`（`require_min_admin_role(EDITOR)` 直接產出） | grade < editor（viewer）發動寫入 | 403 |
| `ValidationError` | `RecordValidationError(BusinessRuleError)` | 欄位不合法（title 空、category 名不存在／inactive、value 非數、sort 欄非法、size/page 非法） | 422 |

> ℹ️ **無「初始 admin 不可寫」特例**：初始/root admin 現為真實 DB `admins` 列（[`bootstrap-hidden-admin.md`](./bootstrap-hidden-admin.md)），寫 records 走一般路徑，`created_by` FK 自然滿足。早期草案的哨兵守衛已作廢。

---

## 1. 背景與目標

前端資料管理頁（`03-data-management.md`）已以 `MockDataSource` 完成列表／分頁／篩選／排序／CRUD／匯入的互動與 AppTest。本 API 提供其真實後端：`ApiDataSource` 呼叫 `/records*`，回應形狀**逐欄對齊前端 dataclass**（`Record` / `Page` / `ImportResult` / `RowError`），使頁面零改動。

### 目標
- 定義 `/records*` 七個端點的方法／路徑／授權／狀態碼／請求與回應 DTO。
- 回應 DTO **忠實承接前端型別**：`Page{items,total,page,size}`（**page/size，非 admin 的 limit/offset**，§7）、`Record`（`category`/`created_by` 為解析後字串）、`ImportResult{created,errors:[{row_index,reason}]}`。
- 定義 DTO ↔ ORM 的解析（`category_id→category` 名、`created_by_principal_id→created_by` username），比照 `admin-management-api.md` §4 的 `from_row` 手法。
- 定義 router 接線（prefix `/records`、`require_min_admin_role` 依賴、`get_record_service` DI）與掛載點。

### 非目標（→ 其他規格）
- **資料表／FK／索引／migration** → [`records-model.md`](./records-model.md)。
- **service／業務規則**（grade 寫入守衛、交易邊界、匯入逐列驗證與 1000 列上限、category 名↔id 解析、sort 正規化）→ `records-service.md`。本規格只描述 **API 可見的契約**（狀態碼、DTO 形狀），實作與單元測試屬 service 層。
- **授權機制**（`grade`、`require_min_admin_role`）→ [`rbac.md`](./rbac.md)。

---

## 2. 授權模型

所有 `/records*` 端點**僅 admin（role=1）可用**；User（role=0）連讀都不行（→ 403）。存取軸為 **AdminRole grade**（`records-model.md` §2.9）：

| grade | 讀取（list/get/categories） | 寫入（create/update/delete/bulk） |
|---|---|---|
| `super_admin` / `editor` | ✅ | ✅ **全部資料**（非擁有權） |
| `viewer` | ✅ | ❌ 403 |

- **讀取端點**：`Depends(require_min_admin_role(AdminRole.VIEWER))`。
- **寫入端點**：`Depends(require_min_admin_role(AdminRole.EDITOR))`——grade < editor（viewer）→ **`ForbiddenError` 403**（即前端 `PermissionDenied`）。
- **無 per-row 授權**：編輯權純 grade-based、對每列一致，前端用 JWT `grade` 全域 disable 按鈕（`03-data-management.md` §權限規則）；後端此依賴為安全底線，**絕不由前端 disable 取代**（model §2.9）。
- **無 bootstrap 特例**：初始/root admin 為真實 DB 列（[`bootstrap-hidden-admin.md`](./bootstrap-hidden-admin.md)），寫 records 走一般 editor+ 路徑，無「初始 admin 不可寫」守衛。

> **router 依賴宣告（比照 `admin/router.py:39`）**：
> ```python
> _require_viewer = require_min_admin_role(AdminRole.VIEWER)
> _require_editor = require_min_admin_role(AdminRole.EDITOR)
> ```

---

## 3. 端點總覽

Router：`APIRouter(prefix="/records", tags=["records"])`，掛載於 `app/api/__init__.py`（`api_router.include_router(record_router)`）。

| # | Method & Path | 用途 | 授權 | 成功碼 | Service |
|---|---|---|---|---|---|
| 1 | `GET /records` | 列表（分頁／分類／關鍵字／排序） | viewer+ | 200 | `list_records` |
| 2 | `GET /records/{id}` | 單筆明細 | viewer+ | 200 | `get_record` |
| 3 | `POST /records` | 建立 | editor+ | 201 | `create_record` |
| 4 | `PATCH /records/{id}` | 更新（四個可編輯欄位） | editor+ | 200 | `update_record` |
| 5 | `DELETE /records/{id}` | 軟刪除 | editor+ | 204 | `delete_record` |
| 6 | `POST /records/bulk` | 批量匯入（逐列驗證，上限 1000） | editor+ | 200 | `bulk_create` |
| 7 | `GET /records/categories` | 分類下拉來源（`is_active`，依 `sort_order`） | viewer+ | 200 | `list_categories` |

> **路由順序**：`/records/categories` 為**靜態路徑**，須宣告在 `/records/{id}` **之前**（否則 `categories` 會被 `{id}` 的 int 轉型攔截成 422/404）。FastAPI 依註冊順序匹配——實作時 categories router 先掛。

---

## 4. DTO／Schema（`app/api/routers/records/schemas.py`）

沿用 codebase 慣例：Pydantic `BaseModel` + `Field` 約束；回應 DTO 以 `from_row` classmethod 承接 repo JOIN 解析的欄位（比照 `AdminSummary.from_row`，`admin/schemas.py:85`）。

### 4.1 回應：`RecordSummary`（對齊前端 `Record`）

```python
class RecordSummary(BaseModel):
    id: int
    title: str
    value: float
    category: str                 # 解析自 category_id → record_categories.name（model §2.4）
    created_by: str               # 解析自 created_by_principal_id → admins.username（model §2.3）
    created_at: datetime
    updated_at: datetime
    note: str
    deleted_at: datetime | None   # include_deleted 時可為非 None
```

- **逐欄對齊前端 `Record` dataclass**（`data-source.md` §資料契約）：`id/title/value/category/created_by/created_at/updated_at/note/deleted_at`。
- **無 `updated_by`**：前端 `Record` 無此欄、頁面不顯示（`03-data-management.md` 列表僅「創建者／建立時間／更新時間」三欄，無「更新者」），model 亦不落此欄（model §2.8）。**契約一致，勿加。**
- **`from_row(row: RecordListRow)`**：`category=row.category_name`、`created_by=row.created_by_username`、其餘取 `row.record.*`（§4.5）。

### 4.2 請求：`RecordCreate` / `RecordUpdate`（**共用 `app/dtos/record.py`，非 router schema**）

比照 users router 直接以 `UserCreate`（`app/dtos/user.py`）為 body 型別：create/update 的 body **直接用 `app/dtos/record.py` 的 `RecordCreate` / `RecordUpdate`**，service 亦收同一型別（`service.create_record(payload, actor)`，UserService 風格）——**不另立 `RecordCreateRequest`**，router 與 service 間零轉換（權威定義：[`records-service.md`](./records-service.md) §2.7）。

```python
# app/dtos/record.py（節錄，權威見 service §2.7）
class RecordCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    value: float = Field(allow_inf_nan=False)            # NaN/Inf → 422
    category: str = Field(min_length=1, max_length=20)   # 分類「名」，service 解析成 category_id（要求 active）
    note: str = Field(default="", max_length=500)

class RecordUpdate(RecordCreate):
    """update 為全量替換四欄，欄位同 create。"""
```

- **只有四個可編輯欄位**（`title/value/category/note`，`data-source.md` §輸入資料）；請求體出現 `id/created_by/時間戳/deleted_at` 一律**被 Pydantic 忽略**（預設 `extra="ignore"`），不報錯——對齊前端「送多餘欄位無妨」。
- **`category` 送「名」（字串）**：如 `"感測器"`，service 以 `get_by_name` 解析成 `category_id`（寫入路徑要求 `is_active=True`，名不存在或 inactive → `RecordValidationError` 422，model §2.7-(1)）。
- **update 為全量替換四欄**：對齊前端編輯彈窗（預填四欄後整包送出，`03-data-management.md` §編輯）。若日後要真 PATCH 部分欄，另議（§11）。
- **Pydantic 硬驗（→ 422）**：`title` 空、`value` 非數、`category` 空／超長——由 DTO 直接 422（前端 `ValidationError`）。語意驗證（category 存在/active）在 service。

### 4.3 回應：`RecordPage`（對齊前端 `Page`）

```python
class RecordPage(BaseModel):
    items: list[RecordSummary]
    total: int      # 篩選後、分頁前筆數（model §2.7-(3)）
    page: int       # 1-based（回傳夾值後的實際頁碼）
    size: int       # 回傳夾值後的實際每頁筆數
```

> **⚠️ 刻意分歧 admin 的 `{items,total,limit,offset}`**：admin API 用 offset/limit 信封（`AdminListResponse`）；**records 必須用 `{items,total,page,size}`（1-based page）**——因前端 `Page` dataclass 就是這四欄（`data-source.md`），`ApiDataSource` 直接 `Page(**resp.json())` 才能零改動。這是**契約驅動的分歧、非疏漏**（§7）。

### 4.4 匯入：`BulkCreateRequest` / `ImportResult` / `RowError`

```python
# router schema（app/api/routers/records/schemas.py）——僅 API 邊界用
class BulkCreateRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(max_length=1000)   # 逐列寬鬆，service 逐列驗證

# app/dtos/record.py（service 回傳型別，service §2.7）——router 直接作 response_model
class RowError(BaseModel):
    row_index: int    # 0-based，對應輸入列序（前端顯示時 +1，03-data-management §匯入）
    reason: str

class ImportResult(BaseModel):
    created: int
    errors: list[RowError]
```

- **`rows` 刻意用 `list[dict]`（寬鬆），不用 `list[RecordCreateRequest]`**：因匯入須**逐列驗證、非法列進 `errors`、不中斷其餘**（`data-source.md` §匯入）。若用嚴格 model，單列壞值會讓 Pydantic 422 掉**整批**、違反契約。故 rows 收原始物件，service 逐列套 create 規則、收集 `RowError`。
- **`max_length=1000`**：超過 1000 列 → **整批 422**（DTO 層即拒，對齊「超限整體拒絕」）。
- **成功碼 200（非 201）**：匯入是**部分成功**語意（`created` + `errors` 並存），非「建立單一資源」，故 200 + `ImportResult`，不回 `Location`。

### 4.5 分類：`Category` + repo 解析列 `RecordListRow`

```python
class Category(BaseModel):
    name: str        # 分類值（前端 CATEGORIES 字串，如 "感測器"）
    label: str       # 下拉顯示文字
    sort_order: int
```

```python
# app/repositories/record.py（model §4）——list/get 的一列：record 本體 + JOIN 解析
@dataclass(frozen=True)
class RecordListRow:
    record: Record
    category_name: str          # JOIN record_categories.name
    created_by_username: str    # JOIN admins.username（恆命中，無 NULL 分支，model §2.3）
```

> **單筆回應也走 `RecordListRow`（統一解析路徑）**：list/get 以 JOIN 帶出 `category_name`／`created_by_username`（免 N+1，model §2.7-(6)／§4）。**create/update 亦然**——雖然 create 的 actor==creator 可就地組 summary，但 **update 的 creator≠actor**（回應要顯示原建立者、非修改者），必經 JOIN 解析；為 create/update 統一解析路徑（DRY），service 一律 commit 後以 `get_active_row(id)` 回 `RecordListRow`（單列查詢，代價可忽略）。詳見 [`records-service.md`](./records-service.md) §3.3。

---

## 5. 端點細節

### 5.1 `GET /records`（list）

**Query 參數**（對齊前端 `list_records` 簽章，`data-source.md` §介面）：

| 參數 | 型別／預設 | 說明 |
|---|---|---|
| `page` | `int = 1`，`Query(ge=1)` | 1-based |
| `size` | `int = 20`，`Query(ge=1)` | 每頁筆數；**不設 `le` 上界**，由 service 夾至 `MAX_PAGE_SIZE=100`（model §2.7-(1)：router 加 `le` 會讓 FastAPI 先 422，夾值形同虛設） |
| `category` | `str \| None = None` | 分類「名」；`None`＝全部；名不存在 → 422。**篩選路徑允許 inactive**（退場分類舊資料仍可篩，model §2.7-(1)） |
| `keyword` | `str \| None = None` | 對 `title` 不分大小寫子字串；空字串視同無篩選；service 跳脫 LIKE 萬用字元 |
| `sort` | `str = "id:asc"` | `"欄位:asc\|desc"`，欄位 ∈ `RecordSortField`（`id/title/value/category/created_at`）；非法 → 422 |
| `include_deleted` | `bool = False` | 預設濾 `deleted_at IS NULL` |

- 回 `200 RecordPage`；`total`＝篩選後、分頁前筆數。**空結果回 `items=[]`、`total` 正確**（前端據此顯示 `empty_state`，`03-data-management.md`）。
- `page` 超出末頁 → `items=[]`、`total` 不變（不 404）。
- **預設 `sort` 取 `DEFAULT_SORT="id:asc"`**（對齊 model §1／頁面 `DEFAULT_SORT`）。註：前端 `DataSource` Protocol 簽章預設寫 `"created_at:desc"`，但頁面實際永遠顯式帶 `sort`，且 `DEFAULT_SORT="id:asc"` 為單一真相——API 預設從後者（doc 級不一致，§11-1）。

### 5.2 `GET /records/{id}`（get）
- `id: int`（path）。查 `WHERE id=? AND deleted_at IS NULL`。
- 命中 → `200 RecordSummary`；不存在或已軟刪 → `RecordNotFoundError` **404**（前端 `RecordNotFound`，編輯/刪除彈窗顯示「資料不存在或已被移除」）。

### 5.3 `POST /records`（create）
- Body `RecordCreate`（`app/dtos`，§4.2）；`Depends(_require_editor)`。
- service：解析 `category` 名→id（要求 active）、`created_by_principal_id = actor.principal_id`。
- 成功 → `201 RecordSummary`（`created_by`＝actor username、時間戳由 DB `server_default`）。
- 例外：viewer → 403；category 名不存在/inactive 或 title 空/value 非數 → 422。

### 5.4 `PATCH /records/{id}`（update）
- Body `RecordUpdate`（`app/dtos`，§4.2）；`Depends(_require_editor)`。
- 只改 `title/value/category/note`；`updated_at` 由 `onupdate` 自動刷新（model §3.5-6）。`created_by`/`created_at` 不動。
- 成功 → `200 RecordSummary`；不存在/軟刪 → 404；viewer → 403；欄位不合法 → 422。

### 5.5 `DELETE /records/{id}`（delete）
- `Depends(_require_editor)`。軟刪除：`SET deleted_at=now()`（model §2.2）。
- 成功 → **`204 No Content`（無 body）**——前端 `delete_record(...) -> None`，`ApiDataSource` 不讀 body。
  > **⚠️ 刻意分歧 admin**（admin `DELETE` 回 200 + summary 供稽核顯示）：records 前端契約回 `None`，故 204 最貼合。若 companion 日後要回軟刪後的 summary，改 200（§11）。
- 不存在/已軟刪 → 404（重複刪冪等與否見 §11-2）；viewer → 403。

### 5.6 `POST /records/bulk`（bulk_create）
- Body `BulkCreateRequest{rows}`；`Depends(_require_editor)`。
- service **逐列**套 create 驗證（title 非空、value 可轉 float、category ∈ active 分類）：合法即建立、非法記 `RowError{row_index(0-based), reason}`、**不中斷其餘**（`data-source.md` §匯入）。
- 成功 → `200 ImportResult{created, errors}`（即使全部失敗也是 200，errors 帶明細；前端據 `errors` 顯示 `st.warning`）。
- `rows` 超 1000 → **422**（DTO `max_length`，整批拒絕）；viewer → 403。
- **交易邊界屬 service 決策**（逐列各自 commit vs 整批一交易，`records-service.md`）；API 只保證回 `ImportResult`。

### 5.7 `GET /records/categories`（list_categories）
- `Depends(_require_viewer)`。回 `is_active=True`、依 `sort_order, name` 排序的 `list[Category]`（model §4 `list_active`）。
- 供前端下拉**動態來源**（取代硬編 `CATEGORIES`）。**非** `DataSource` Protocol 方法——目前前端下拉仍用常數 `CATEGORIES`，故此端點**不影響零改動**，是為 Phase 2+ 動態分類與後台管理預留（§11-3）。

---

## 6. 狀態碼 & 例外映射

全域 handler（`app/core/exceptions/handlers.py`）統一輸出 `{"error","message","request_id"[,"details"]}`。records 端點映射：

| 情境 | 例外 | HTTP |
|---|---|---|
| 未認證 / token 失效 / 帳號不可用 | `UnauthorizedError` | 401 |
| grade < editor 發動寫入 ／ 非 admin（role=0）呼叫任何 `/records*` | `ForbiddenError` | 403 |
| record 不存在 / 已軟刪除（get/update/delete） | `RecordNotFoundError`(→`NotFoundError`) | 404 |
| DTO 欄位驗證（title 空、value 非數、size/page<1、rows>1000、path id 非 int） | pydantic | 422 |
| 語意驗證（category 名不存在/寫入用 inactive、sort 欄非法） | `RecordValidationError`(→`BusinessRuleError`) | 422 |

> **無 409**：records 無 username 之類唯一鍵衝突（匯入不去重，model §9-6）；分類名唯一衝突屬後台分類管理（§11-3），不在本批端點。

---

## 7. 前端契約對齊與「零改動」保證

`ApiDataSource` 上線後前端頁面／AppTest 零改動的**逐項核對**：

| 前端型別／行為 | 本 API 對應 | 一致性 |
|---|---|---|
| `Page{items,total,page,size}`（1-based） | `RecordPage`（§4.3） | ✅ **刻意用 page/size，非 admin 的 limit/offset** |
| `Record{id,title,value,category:str,created_by:str,created_at,updated_at,note,deleted_at}` | `RecordSummary`（§4.1） | ✅ 逐欄對齊；`category`/`created_by` 為解析後字串 |
| `ImportResult{created,errors:[{row_index,reason}]}` | `ImportResult`/`RowError`（§4.4） | ✅ `row_index` 0-based |
| `RecordNotFound`→404 / `PermissionDenied`→403 / `ValidationError`→422 | §6 映射 | ✅ |
| `list_records` 六參數 | `GET /records` query（§5.1） | ✅ 同名同義（page/size/category/keyword/sort/include_deleted） |
| create 送四欄、來源指派 `created_by`/時間戳 | §5.3 | ✅ |
| delete 回 `None` | 204（§5.5） | ✅ |

> **唯一新增**：`ApiDataSource` 實作類別 + 其單元測試（mock HTTP 回應），既有頁面／`DataSource` 介面／`can_edit`／型別／例外皆不動（`data-source.md` §換成真實 API 時要改什麼）。

---

## 8. TDD 整合測試計畫（`tests/integration/test_records_api.py`；先寫、先看到 RED）

依 `CLAUDE.md` Red→Green→Refactor，`httpx` ASGI client + conftest `client`/`db_session`/`admin` fixtures；以 `_mk(db, username, role)` 造不同 grade admin、`_login` 取 token、`_auth(token)` 帶 header（比照 `test_admin_management_api.py`）。**須先有 `record_categories` 四分類 fixture**（model §6 測試環境）。

### 8.1 授權
- viewer 讀 `GET /records` → 200；viewer 寫 `POST/PATCH/DELETE/bulk` → **403**。
- editor / super_admin 寫 → 成功（201/200/204/200）。
- 無 token → 401。

### 8.2 列表（§5.1）
- 分頁：`page`/`size` 切片正確；`total`＝篩選後筆數；`page` 超末頁回 `items=[]`。
- **`size` 夾上限**：`size=10^6` → 回應 `size=100`（service 夾，非 422）。
- 篩選：`category` 精確；`keyword` 不分大小寫子字串；**inactive 分類仍可篩出舊資料**。
- 排序：各 `RecordSortField` asc/desc；`sort=category` 依分類**名**；**非法 sort 欄 → 422**。
- `include_deleted`：預設不含軟刪列；`=true` 帶出。

### 8.3 單筆／建立／更新／刪除
- `GET /records/{id}` 命中 200；不存在／軟刪 → 404。
- `POST` 201，回應 `created_by`＝登入 admin username、`category` 為字串；category 名不存在 → 422。
- `PATCH` 200 改四欄、`updated_at` 前進、`created_by` 不變；不存在 → 404。
- `DELETE` 204 無 body；再 `GET` 該 id → 404（已軟刪）。

### 8.4 匯入（§5.6）
- 全合法 → `created=N, errors=[]`；混入非法列 → 該列進 `errors`（`row_index` 0-based）、合法列仍建立；**200**（即使有 errors）。
- `rows` > 1000 → 422（整批拒）。

### 8.5 分類 & 回應形狀
- `GET /records/categories` → 只含 `is_active` 且依 `sort_order` 排；欄位 `name/label/sort_order`。
- `RecordSummary` 形狀鎖定：斷言含 `id/title/value/category/created_by/created_at/updated_at/note/deleted_at`、**不含 `updated_by`**（防日後誤加破壞契約）。

### 8.6 初始 admin（真實列）
- **無哨兵守衛測試**（初始 admin 已是真實 DB 列，`bootstrap-hidden-admin.md`）。可選：以 root actor（`bootstrap-hidden-admin.md` 的 opt-in seed fixture）建 record → 正常 201，驗「root 也能寫 records」。

---

## 9. 實作順序（TDD 里程碑）

> service 層（`records-service.md`）與 model（`records-model.md`）為前置。API 層由外而內補測試：

1. Router 骨架 + `GET /records/categories`（最單純的讀，驗授權 + 形狀）。
2. `GET /records`（分頁/篩選/排序 query → service；驗 §8.2）。
3. `GET /records/{id}`、`POST`、`PATCH`、`DELETE`（單筆 CRUD + 404/403，§8.3）。
4. `POST /records/bulk`（逐列 errors + 1000 上限，§8.4）。
5. `RecordSummary` 形狀鎖定測試（§8.5，防契約漂移）。

---

## 10. 已定案決策（草案）

- ✅ **端點集與授權**（§2／§3）：`/records*` 僅 admin；讀 viewer+、寫 editor+、皆走 `require_min_admin_role`；bulk/categories 同軸。
- 🔄 **分頁信封用 `{items,total,page,size}`（page/size、1-based）**（§4.3／§7）：**刻意分歧** admin 的 `{items,total,limit,offset}`——因前端 `Page` dataclass 即此四欄，零改動的硬約束。size 夾值在 service、router `size` 不設 `le`（對齊 model §2.7-(1)）。
- ✅ **回應 `RecordSummary` 不含 `updated_by`**（§4.1）：對齊前端 `Record` 契約與 model §2.8（不落此欄）；§8.5 加形狀鎖定測試防誤加。
- ✅ **DELETE 回 204（無 body）**（§5.5）：對齊前端 `delete_record -> None`；刻意分歧 admin 的 200+summary。
- ✅ **bulk 回 200 + `ImportResult`、rows 用寬鬆 `list[dict]`**（§4.4／§5.6）：逐列驗證、非法進 `errors`、不中斷；1000 上限由 DTO `max_length` 整批 422。這是 codebase **首個批量端點**（無先例，自訂逐列語意）。
- ✅ **`category` 在 API 邊界為「名」字串**（§4.2／§5.1）：請求送名、回應回名；`category_id` 解析封裝在 service/repo（model §2.4），前端只見字串、零改動。
- ✅ **`Record*` 例外繼承既有基類**（§0／§6）：`RecordNotFoundError(NotFoundError)`／`RecordValidationError(BusinessRuleError)`；權限拒絕直接由 `require_min_admin_role(EDITOR)` 產 `ForbiddenError`。
- ✅ **`/records/categories` 宣告在 `/records/{id}` 前**（§3）：避免靜態路徑被 `{id}` 攔截。
- ✅ **create/update body 直接用 `app/dtos/record.py` 的 `RecordCreate`/`RecordUpdate`**（§4.2）：UserService 風格——router 與 service 共用同一 Pydantic 型別、零轉換，不另立 `RecordCreateRequest`（service §2.7/§8）。`ImportResult`/`RowError` 同放 dtos（service 回傳型別）。

## 11. 待確認事項（Open Questions）

1. **list 預設 `sort`**：本規格取 `"id:asc"`（＝`DEFAULT_SORT`／model §1）；前端 `DataSource` Protocol 簽章預設寫 `"created_at:desc"`（doc 級不一致，頁面實際永遠顯式帶 sort）。是否回頭統一前端 Protocol 簽章預設？
2. **重複 DELETE 冪等性**：已軟刪的 id 再 DELETE → 404 還是 204（冪等）？本草案傾向 404（對齊 get「軟刪即不存在」）；admin delete 為冪等 return，語意可再對齊。
3. **`GET /records/categories` 與後台分類管理 CRUD**：本批只提供唯讀下拉來源；分類的新增／改名／改排序／停用（`record_categories` 寫入端點與授權）屬後續 companion，端點命名（`/records/categories` vs 獨立 `/admin/categories`）待定。
4. **update 全量 vs 真部分 PATCH**：本草案 `PATCH` 收全四欄（對齊前端整包送出）；若日後要單欄更新，改 body 為 `Optional` 欄並在 service 做部分更新。
5. **匯入交易邊界**：逐列各自 commit（部分成功即落地）vs 整批一交易（全成功才落地）——屬 `records-service.md`，影響「錯誤列存在時 created 的列是否已持久化」的語意，需與前端「成功 N 筆」文案對齊。
6. **`bulk` 回應是否附成功列明細**：目前只回 `created`(數量)；若前端日後要顯示新建 id 清單，擴 `ImportResult`（不影響現有零改動）。

---

## 12. 實作接線清單（檔案落點與註冊）

| 產物 | 檔案 | 註冊 / 匯出 |
|---|---|---|
| Router | `app/api/routers/records/router.py`（新檔；`APIRouter(prefix="/records", tags=["records"])`） | ① `app/api/routers/__init__.py`：`from .records import router as records_router` + 加入 `__all__`；② `app/api/__init__.py`：`api_router.include_router(records_router)` |
| 回應 schemas（`RecordSummary`／`RecordPage`／`Category`／`BulkCreateRequest`） | `app/api/routers/records/schemas.py`（新檔，§4） | — |
| 請求 body 型別 | **直接 import `app/dtos/record.py`** 的 `RecordCreate`/`RecordUpdate`（§4.2，無 router 端請求 schema） | — |
| 整合測試 | `tests/integration/test_records_api.py`（§8） | — |

> 路由宣告順序：`/records/categories` 在 `/records/{id}` 之前（§3）。service／dtos／exceptions／enums／config 的落點見 service §10；models／repositories／migration 見 model §10。
