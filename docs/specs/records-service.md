# 規格書（Service 層）：資料記錄（Record）管理 — 業務規則

> 狀態：**草案（尚未實作）** ／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「資料記錄管理」的 **Service 層**（業務規則、輸入正規化、交易邊界）。資料表見 [`records-model.md`](./records-model.md)；HTTP／DTO／授權接線見 [`records-api.md`](./records-api.md)。三者為 model → service → api 三分法（比照 `admin-management-{model,service,api}.md`）。
>
> 🔗 **契約來源**：本 service 是前端 [`data-source.md`](../../../StreamSightStreamlit/docs/specs/data-source.md) 之 `MockDataSource` **行為規格**的後端對應——mock 於記憶體做的分頁/篩選/排序/逐列匯入/權限，本層以 DB + repo 忠實重現，讓 `ApiDataSource` 上線後行為一致。

---

## 0. 功能總覽（先讀這裡）

**一句話**：`RecordService` 承接 API 傳入的參數，做**輸入正規化與業務驗證**（fail-closed），呼叫 repo 純查詢/寫入，回傳已解析（category 名、created_by username）的列給 API。**授權（grade 階梯）在 router，本層負責業務規則**。

**本層職責邊界**：

| 做什麼（service） | 不做什麼 |
|---|---|
| size/page 夾值、sort 字串→enum、keyword LIKE 跳脫、category 名→id 解析（model §2.7-(1)） | grade 授權（router `require_min_admin_role`，§2.4） |
| 交易邊界、軟刪除設 `deleted_at`、匯入逐列驗證 | SQL 謂詞/ORDER BY/JOIN 構造（repo，model §4） |
| 匯入逐列驗證、1000 上限、收集 `RowError`、不中斷 | HTTP 狀態碼/DTO 形狀（api §4/§6） |
| 交易邊界（單次 commit / 整批一交易）、軟刪除設 `deleted_at` | 加解密、時間戳（DB `server_default`/`onupdate`） |

**核心方法（→ api 端點）**：

| Service 方法 | 對應端點 | 回傳 |
|---|---|---|
| `list_records(...)` | `GET /records` | `(rows: Sequence[RecordListRow], total, page, size)` |
| `get_record(id)` | `GET /records/{id}` | `RecordListRow`（404→`RecordNotFoundError`） |
| `create_record(payload, actor)` | `POST /records` | `RecordListRow` |
| `update_record(id, payload, actor)` | `PATCH /records/{id}` | `RecordListRow` |
| `delete_record(id, actor)` | `DELETE /records/{id}` | `None` |
| `bulk_create(rows, actor)` | `POST /records/bulk` | `ImportResult` |
| `list_categories()` | `GET /records/categories` | `Sequence[RecordCategory]` |

---

## 1. 背景與目標

前端 `MockDataSource`（`data-source.md` §行為規格）在記憶體完成：分頁（篩選→排序→切片、`total`＝篩選後筆數）、篩選（category 精確、keyword 不分大小寫子字串、預設排除軟刪）、排序（`sort` 解析、非法欄→`ValidationError`）、CRUD（權限、軟刪除設 `deleted_at`、更新刷 `updated_at`）、匯入（逐列驗證、非法進 `errors`、1000 上限）。本層以 `RecordService` + repo 提供**同語意**的持久化實作。

### 目標
- 定義 `RecordService` 的方法簽章、**輸入正規化**（model §2.7-(1)）、業務驗證、交易邊界。
- 定義跨層共用詞彙／常數（`RecordSortField`／`SortDirection`／`MAX_PAGE_SIZE`…）的落點與解析規則。
- 定義匯入逐列驗證與 `ImportResult` 組裝（`data-source.md` §匯入）。
  > ℹ️ **無 bootstrap 守衛**：初始 admin 現為真實 DB 列（`bootstrap-hidden-admin.md`），`created_by` FK 自然滿足，**不需擋初始 admin 寫入**（早期草案的哨兵守衛已作廢）。
- 定義匯入逐列驗證與 `ImportResult` 組裝（`data-source.md` §匯入）。

### 非目標
- 資料表結構 → [`records-model.md`](./records-model.md)；HTTP/DTO/授權 → [`records-api.md`](./records-api.md)；grade 機制 → [`rbac.md`](./rbac.md)。

---

## 2. 共用元件

### 2.1 Enums（新增於 `app/core/enums.py`，`StrEnum` 對齊 `AdminRole`/`AdminStatusFilter`）

```python
class RecordSortField(StrEnum):
    """Record 可排序欄位白名單，值對映前端 SORTABLE（契約不變，model §1/§4）。"""
    ID = "id"
    TITLE = "title"
    VALUE = "value"
    CATEGORY = "category"
    CREATED_AT = "created_at"

class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"
```

> **⚠️ 值必為 `id/title/value/category/created_at`**（前端 `SORTABLE`）——**不含 `updated_at`**（前端 SORTABLE 無此欄，且 records 無 `updated_by`，model §2.8）。這是封閉白名單、驗證單一真相；repo 收 enum 即不可能非法欄名（model §4）。

### 2.2 常數（`app/core/config`，比照 `monitoring_query_max_limit`）

於 `BaseAppSettings` 新增（可由 env 調）：

```python
records_max_page_size: int = Field(default=100, ge=1, le=1000, description="Records 列表每頁上限")
records_default_page_size: int = Field(default=20, ge=1, description="Records 列表預設每頁筆數")
```

模組常數（`app/core/enums.py` 或 records 專用常數檔）：

```python
DEFAULT_SORT = "id:asc"   # list 未帶 sort 時套用（＝前端 DEFAULT_SORT，model §1）
```

### 2.3 `actor: Admin`（寫入方法收 Admin 物件，非僅 principal_id）

寫入方法（create/update/delete/bulk）收**當前 admin 物件** `actor: Admin`（來自 router 的 `require_min_admin_role(EDITOR)` 依賴，其回傳即 `Admin`，api §2）。

> **⚠️ 刻意與 `AdminService` 分歧**（後者收 `actor_principal_id: int | None`）：records 寫入需要 actor 的**兩樣東西**——`actor.principal_id`（寫 `created_by_principal_id`）與 `actor.username`（組回應免多一次查詢）。收整個 `Admin` 一次到位，比只收 id 再回查 username 乾淨。**初始/root admin 現亦為真實 `Admin` DB 列**（`bootstrap-hidden-admin.md`，非合成哨兵），`principal_id` 為自增值、存在於 `admins`，寫入無特例。

### 2.4 授權分層：grade 在 router，本層不做 actor 特判

- **grade 階梯授權不在本層**：讀 viewer+、寫 editor+ 由 router `require_min_admin_role(...)` enforce（api §2）——與 `AdminService` 一致（role 授權在 router Depends）。本層**信任傳入的 actor 已通過 grade 檢查**。
- **無 bootstrap 守衛**：初始 admin 為真實 DB 列（`bootstrap-hidden-admin.md`），`created_by_principal_id=<真實 id>` FK 自然滿足，**不需擋初始 admin**。早期草案的哨兵守衛（`principal_id=0` → `BusinessRuleError`）已作廢。

### 2.5 交易邊界（比照 `AdminService`：commit 在 service）

- 每個 mutation **單次 `await self.session.commit()`**，`try/except: rollback; raise` 包裹（`admin.py` create 形狀）。repo 只 `flush`/`add`，不 commit。
- 讀取方法（list/get/categories）**無 commit**。
- 匯入為**整批一交易**（合法列一起落地，§3.6）。

### 2.6 Service 建構（DI）

```python
class RecordService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = RecordRepository(session)
        self.category_repo = RecordCategoryRepository(session)
```

DI provider（`app/api/dependencies/services.py`，比照 `get_user_service`）：

```python
def get_record_service(session: AsyncSession = Depends(get_session)) -> RecordService:
    return RecordService(session)
```

> 無 `Publisher`（records 無「撤 token/踢 WS」需求，與 `AdminService` 不同）。

### 2.7 DTO：`RecordCreate` / `RecordUpdate` / `RowError` / `ImportResult`（`app/dtos/record.py`，新檔）

比照 `UserService.create(payload: UserCreate)`（`app/dtos/user.py`）：**service 收 `app/dtos` 的 Pydantic DTO，router body 直接用同一型別**（api §4.2），不另立 router request schema、不拆 kwargs——codebase 兩種風格並存（`UserService` 收 DTO、`AdminService` 收 kwargs），records 採前者：create/update 同四欄，DTO 一次定義兩處共用，比四個 kwargs 拆傳乾淨。

```python
# app/dtos/record.py
class RecordCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    value: float = Field(allow_inf_nan=False)            # NaN/Inf 進 Float 欄會毒化排序/顯示 → 422
    category: str = Field(min_length=1, max_length=20)   # 分類「名」，service 解析成 id（§3.7）
    note: str = Field(default="", max_length=500)

class RecordUpdate(RecordCreate):
    """update 全量替換四欄（api §4.2），欄位同 create。"""

class RowError(BaseModel):
    row_index: int   # 0-based，對應輸入列序
    reason: str

class ImportResult(BaseModel):
    created: int
    errors: list[RowError]
```

- **`ImportResult`/`RowError` 放 dtos 而非 router schema**：它們是 **service 回傳型別**（§3.6）——放 api 層會讓 service import router（分層倒置）。router 的 `response_model` 直接引用。
- 一併匯出於 `app/dtos/__init__.py`。

---

## 3. Service 方法

### 3.1 `list_records`（讀取路徑最佳實踐，model §2.7）

```python
async def list_records(
    self, *, page: int, size: int,
    category: str | None, keyword: str | None,
    sort: str, include_deleted: bool,
) -> tuple[Sequence[RecordListRow], int, int, int]:  # (rows, total, page, size)
```

**正規化與驗證（fail-closed，model §2.7-(1)）——依序**：

1. **夾值**：`size = min(max(size, 1), MAX_PAGE_SIZE)`；`page = max(page, 1)`。**回傳夾值後的 page/size** 供 API 回應（前端 `Page.page/size` 顯示實際值，api §4.3）。
2. **sort 解析成 enum**（唯一驗證點）：`_parse_sort(sort)`（§3.7）→ `(RecordSortField, SortDirection)`；非法欄名／方向 → `RecordValidationError`（422）；空字串套 `DEFAULT_SORT`。
3. **keyword 跳脫**：`_escape_like(keyword)`（§3.7）；空字串 → `None`（視同無篩選）。
4. **category 名→id（篩選路徑，允許 inactive）**：`category` 非 None → `category_repo.get_by_name(category)`；`None`（名不存在）→ `RecordValidationError`；**不檢查 `is_active`**（退場分類舊資料仍可篩，model §2.7-(1)）。取其 `id` 當 `category_id`。

**查詢（委派 repo，收乾淨參數）**：

```python
offset = (page - 1) * size
rows = await self.repo.list_records(
    category_id=category_id, keyword=escaped_kw,
    sort_field=sort_field, sort_dir=sort_dir,
    include_deleted=include_deleted, limit=size, offset=offset,
)
total = await self.repo.count_records(
    category_id=category_id, keyword=escaped_kw, include_deleted=include_deleted,
)  # 與 list 共用謂詞建構器（model §4）
return rows, total, page, size
```

- `rows` 為 `RecordListRow`（record + 解析出的 `category_name`／`created_by_username`，api §4.5），供 API 直接組 `RecordSummary`（免 N+1，model §2.7-(6)）。
- `total`＝篩選後、分頁前筆數。`page` 超末頁 → `rows=[]`、`total` 不變（不報錯）。

### 3.2 `get_record`

```python
async def get_record(self, record_id: int) -> RecordListRow:
    row = await self.repo.get_active_row(record_id)   # WHERE id=? AND deleted_at IS NULL + JOIN 解析
    if row is None:
        raise RecordNotFoundError(f"Record {record_id} not found")
    return row
```

- **回 `RecordListRow`（含解析）**，與 list 共用同一套解析（DRY，model §4「列表與單筆共用」）。
- 不存在或已軟刪 → `RecordNotFoundError`（404，前端 `RecordNotFound`）。
  > **repo 增補（承 model §4）**：model §4 列了 `get_active(id) -> Record | None`（裸列，供純存在檢查）；本層另需 **`get_active_row(id) -> RecordListRow | None`**（同 JOIN 解析的單筆版），使 get 回應與 list 一致。兩者可共用同一 select 建構器（單筆多一個 `WHERE id=?`）。

### 3.3 `create_record`

```python
async def create_record(self, payload: RecordCreate, actor: Admin) -> RecordListRow:
```

1. **category 名→id（寫入路徑，要求 active）**：`_resolve_writable_category(payload.category)`（§3.7）→ `category_id`；名不存在或 `is_active=False` → `RecordValidationError`(422)。
3. **欄位驗證**：`title` 非空（strip 後）、`value` 可轉 float（DTO 已擋大部分，service 對 bulk 的 dict 亦驗，§3.6）。
4. INSERT：`Record(title, value, category_id, created_by_principal_id=actor.principal_id, note)`；`repo.add(record)`（flush）→ `session.commit()`。
5. 回 `repo.get_active_row(record.id)`（統一解析路徑，`created_by_username=actor.username`、`category_name` 已知）。

> **為何 commit 後再 `get_active_row` 而非就地組**（修訂 api §4.5 的樂觀說法）：create 時 actor==creator，可就地組；但**update 的 creator≠actor**（§3.4），須 JOIN 解析原建立者 username。為兩者**統一解析路徑**（DRY、避免 create/update 回應組法分岐），一律 commit 後走 `get_active_row`。多一次 SELECT（單列、有 PK/index），代價可忽略。

### 3.4 `update_record`

```python
async def update_record(self, record_id: int, payload: RecordUpdate, actor: Admin) -> RecordListRow:
```

1. **取列**：`record = await self.repo.get_active(record_id)`；`None` → `RecordNotFoundError`(404)。
3. **category 名→id（要求 active，§3.7）**。
4. **只改四欄**：`title/value/category_id/note`（`created_by`/`created_at` 不動；`updated_at` 由 `onupdate` 自動刷新，model §3.5-6）。**不設 `updated_by`**（無此欄，model §2.8）。
5. `session.commit()` → 回 `repo.get_active_row(record_id)`（`created_by_username` 為**原建立者**，非 actor，故必經 JOIN 解析）。

### 3.5 `delete_record`

```python
async def delete_record(self, record_id: int, actor: Admin) -> None:
    record = await self.repo.get_active(record_id)
    if record is None:
        raise RecordNotFoundError(f"Record {record_id} not found")
    record.deleted_at = datetime.now(UTC)                # 軟刪除（model §2.2）
    await self.session.commit()
```

- 回 `None`（API 204，前端 `delete_record -> None`）。
- 已軟刪的 id → `get_active` 回 `None` → 404（重複刪冪等性見 api §11-2）。

### 3.6 `bulk_create`（逐列驗證，`data-source.md` §匯入）

```python
async def bulk_create(self, rows: list[dict], actor: Admin) -> ImportResult:
```

1. **1000 上限**：`len(rows) > 1000` → `RecordValidationError`(422)（DTO `max_length` 已擋，service 防禦性再擋——直接呼叫 service 的測試/腳本亦安全）。
3. **逐列驗證**（`_validate_row`，§3.7）：對每列（0-based `i`）套 create 規則——`title` 非空、`value` 可轉 float、`category` ∈ **active** 分類。
   - 合法 → 收集待建 `Record`（`created_by_principal_id=actor.principal_id`）。
   - 非法 → `errors.append(RowError(row_index=i, reason=...))`，**不中斷其餘**。
4. **整批一交易落地合法列**：`repo.bulk_insert(valid_records)` → `session.commit()`。
5. 回 `ImportResult(created=len(valid_records), errors=errors)`。

> **交易語意（定案）**：合法列**整批一交易**（全落地或全 rollback），非法列僅報告——`created` 反映實際落地列數。若某合法列在 INSERT 階段才觸發 DB 錯誤（驗證後仍失敗，理論上罕見）→ 整批 rollback、例外上拋（不部分落地）。「逐列各自 commit」的替代語意見 api §11-5。

### 3.7 私有輔助（正規化與解析）

- `_parse_sort(sort: str) -> tuple[RecordSortField, SortDirection]`：`sort or DEFAULT_SORT` → 拆 `"field:dir"`；`RecordSortField(field)`／`SortDirection(dir)`，**`ValueError` → `RecordValidationError`**（唯一把字串轉 enum 並翻譯例外的點；缺 `:` 或多段亦 422）。
- `_escape_like(keyword: str | None) -> str | None`：`None`/空/純空白 → `None`；否則對 `%` `_` `\` 前綴加 `\`、回 `f"%{kw.strip().lower()}%"`（配 repo 的 `ESCAPE '\'`，model §2.5／§2.7-(1)）。
- `_resolve_writable_category(name: str) -> int`：`category_repo.get_by_name(name)`；`None` 或 `is_active=False` → `RecordValidationError`（寫入不得用不存在/退場分類，model §2.4）。回 `id`。
- `_validate_row(row: dict) -> Record | RowError`：匯入單列驗證，重用上述欄位規則 + `_resolve_writable_category`；`value` 接受 int/float/**str**（CSV 解析產物皆為字串），以 `float(value)` 強制轉換——轉換失敗、`NaN`、`±Inf` → `RowError`（fail-closed，同 DTO `allow_inf_nan=False`，§2.7）；多餘欄位忽略（`data-source.md` §匯入）。

### 3.8 `list_categories`

```python
async def list_categories(self) -> Sequence[RecordCategory]:
    return await self.category_repo.list_active(order_by_sort=True)  # is_active, ORDER BY sort_order, name
```

- 純讀取、無正規化（model §4 `list_active`）。供 `GET /records/categories`（api §5.7）。

---

## 4. 依賴注入

`get_record_service`（§2.6）掛於 router 各端點 `service: RecordService = Depends(get_record_service)`；actor 另由 `Depends(require_min_admin_role(...))` 提供並傳入寫入方法（api §2）。

---

## 5. 安全性考量（service 層）

- **初始 admin 為真實 DB 列 → 無 FK 陷阱**：root 由 `bootstrap-hidden-admin.md` 落地成真列，`created_by_principal_id=<真實 id>` FK 自然滿足，**本層不需 bootstrap 守衛**（早期草案的哨兵守衛已作廢）。
- **輸入正規化 fail-closed**（§3.1）：size 夾 `[1,100]` 防 DoS；sort 走 enum 白名單、絕不拼字串進 ORDER BY（repo 端映射 Column，model §5）；keyword 參數綁定 + LIKE 跳脫防注入與誤配。
- **授權不在本層但必存在**：grade 由 router enforce（§2.4）；本層信任 actor——**若日後有非 HTTP 呼叫端**（script/job）直接用 service，須自行確保 actor 合法（同 `AdminService` 慣例）。
- **匯入邊界**：1000 上限（§3.6）+ 逐列驗證，避免超大批次與髒資料落地。

---

## 6. TDD 測試計畫（`tests/unit/services/test_record.py`；先寫、先看到 RED）

用 `db_session` fixture 直接 new `RecordService(db_session)`；**須先種 `record_categories` 四分類**（model §6 fixture）；造 admin actor 用既有 `admin` fixture 或 `AdminService.create`。`pytest.raises(...)` 斷言例外、`db_session.execute(select(...))` 驗後置狀態（比照 `test_admin.py`）。

### 6.1 正規化（§3.1／§3.7）
- **size 夾值**：`size=10**6` → 回傳 `size=100`；`size=0`/`page=0` → `size=1`/`page=1`（回傳夾值後值）。
- **sort 解析**：`"title:desc"` → `(TITLE, DESC)`；`"bogus:asc"` → `RecordValidationError`；`"id:sideways"` → `RecordValidationError`；`""` → 套 `DEFAULT_SORT`（`id:asc`）。
- **keyword 跳脫**：含 `%`/`_`/`\` → 當**字面**（配 repo ESCAPE 驗字面比對）；空白字串 → 視同無關鍵字。
- **category 分流**：篩選路徑接受 inactive、名不存在 → 422；**寫入路徑**（create/update/bulk）拒 inactive → 422。

### 6.2 CRUD
- `create_record`：合法 → 落地、`created_by_principal_id=actor.principal_id`、回應 `created_by`＝actor username、`category` 為字串。
- `get_record`／`update_record`／`delete_record`：不存在或已軟刪 → `RecordNotFoundError`；update 只改四欄、`updated_at` 前進、`created_by` 不變、回應 `created_by` 為**原建立者**（非 actor）；delete 設 `deleted_at`、再 get → 404。

### 6.3 匯入（§3.6）
- 全合法 → `created=N, errors=[]`、N 列落地。
- 混入非法列（title 空／value 非數／category 不存在或 inactive）→ 該列進 `errors`（`row_index` 0-based）、合法列仍落地、**不中斷**。
- `rows` > 1000 → `RecordValidationError`、**零列落地**。

### 6.4 ~~bootstrap 守衛~~ — **已移除**
初始 admin 現為真實 DB 列（`bootstrap-hidden-admin.md`），`created_by` FK 自然滿足，**無哨兵守衛與其測試**。初始 admin 寫 records 走一般路徑（可用 `bootstrap-hidden-admin.md` 的 opt-in seed fixture 造 root actor 驗證「root 也能正常建 record」，非必要）。

---

## 7. 實作順序（TDD 里程碑）

> model（表 + repo）為前置。本層由內而外：

1. Enums（`RecordSortField`/`SortDirection`）+ config 常數 + `_parse_sort`/`_escape_like` 純函式（先測正規化，§6.1）。
2. `RecordService` 骨架 + `get_record`/`create_record`（含 `_resolve_writable_category`、`get_active_row`）。
3. `list_records`（夾值 + 委派 repo + total，§6.1/§6.2）。
4. `update_record`/`delete_record`（軟刪除、404、只改四欄，§6.2）。
5. `bulk_create`（逐列驗證、1000 上限、整批交易，§6.3）。
6. `list_categories`。

---

## 8. 已定案決策（草案）

- ✅ **職責邊界**（§0/§2.4）：grade 授權在 router；service 做正規化 + 業務驗證 + 交易。與 `AdminService`（role 在 router、業務守衛在 service）一致。**無 bootstrap 守衛**（初始 admin 已是真實 DB 列，`bootstrap-hidden-admin.md`）。
- 🔄 **寫入方法收 `actor: Admin`（非 `actor_principal_id: int`）**（§2.3）：**刻意分歧** `AdminService`——records 需 actor 的 `principal_id`（寫 `created_by`）與 `username`（組回應），收整個 `Admin` 一次到位。初始 admin 亦為真實 `Admin` DB 列，無特例。
- ✅ **統一解析路徑：write 後 `get_active_row` 回 `RecordListRow`**（§3.3/§3.4）：修訂 api §4.5「單筆寫入免查詢」的樂觀說法——因 update 的 creator≠actor 必經 JOIN，為 DRY 一律 commit 後走 `get_active_row`（單列查詢，代價可忽略）。
- ✅ **sort 驗證唯一點在 service**（§3.7）：`_parse_sort` 把字串轉 enum、`ValueError→RecordValidationError`；repo 收 enum 不再驗（model §4）。
- ✅ **category 解析分流**（§3.1/§3.7）：篩選允許 inactive、寫入要求 active、名不存在兩路皆 422。
- ✅ **匯入整批一交易 + 逐列驗證 + 1000 上限**（§3.6）：合法列原子落地、非法列進 `errors` 不中斷；`created` 反映實際落地數。
- ✅ **無 bootstrap 守衛**（§2.4）：初始 admin 為真實 DB 列（`bootstrap-hidden-admin.md`）→ `created_by` FK 自然滿足；早期哨兵守衛（`principal_id=0` → `BusinessRuleError`）已作廢移除。
- ✅ **新增 config 常數 + enums**（§2.1/§2.2）：`RecordSortField`/`SortDirection` 值＝前端 SORTABLE（`id/title/value/category/created_at`，**無 updated_at**）；`records_max_page_size=100`/`records_default_page_size=20`/`DEFAULT_SORT="id:asc"`。
- ✅ **service 收 `app/dtos` DTO（UserService 風格）**（§2.7）：`create_record(payload: RecordCreate, actor)`；router body 直接用同一型別（api §4.2），不另立 request schema、不拆 kwargs（`AdminService` 風格不採——codebase 兩風格並存，records 取 DTO 因 create/update 同四欄、兩處共用一次定義）。`ImportResult`/`RowError` 同放 dtos（service 回傳型別放 api 層會倒置分層）。
- ✅ **`value` 拒 NaN/Inf**（§2.7/§3.7）：DTO `Field(allow_inf_nan=False)`；bulk `_validate_row` 同擋（NaN 進 DB `Float` 會毒化排序與前端顯示）。

## 9. 待確認事項（Open Questions）

1. **匯入交易語意**：整批一交易（本草案）vs 逐列各自 commit（部分落地）——影響「有錯誤列時已建立列是否持久化」，須與前端「成功 N 筆」文案對齊（同 api §11-5）。
2. **重複 DELETE 冪等性**：已軟刪 id 再 delete → 404（本草案）vs 冪等 return（同 api §11-2）。
3. ~~**`value` 是否拒 NaN/Inf**~~ → **已定案：拒收**（DTO `allow_inf_nan=False`、bulk `_validate_row` 同擋，§2.7／§3.7／§8）。是否另需**範圍**檢查（負值、上下限）仍開放，依業務語意再議（model §2.6 型別為 `Float`）。
4. **是否提供 restore（軟刪復原）**：前端 mock 為單向刪除；若後台需要，加 `restore_record`（清 `deleted_at`）+ 對應 repo/守衛（model §9-3）。
5. **非 HTTP 呼叫端的授權**：若日後有 job/script 直接用 `RecordService`，grade 不再由 router 把關——是否需 service 層可選的 grade 斷言（§5）。

---

## 10. 實作接線清單（檔案落點與註冊）

| 產物 | 檔案 | 註冊 / 匯出 |
|---|---|---|
| `RecordCreate` / `RecordUpdate` / `RowError` / `ImportResult` | `app/dtos/record.py`（新檔，§2.7） | 匯出於 `app/dtos/__init__.py` |
| `RecordNotFoundError(NotFoundError)`、`RecordValidationError(BusinessRuleError)` | `app/core/exceptions/record.py`（新檔；`base.py` 維持純通用例外） | 匯出於 `app/core/exceptions/__init__.py`。**全域 handler 零改動**——status_code/error_code 由基類承接（handlers 只認 `AppException`） |
| `RecordSortField` / `SortDirection` | `app/core/enums.py`（§2.1，加於既有 StrEnum 之後） | — |
| `records_max_page_size` / `records_default_page_size` | `app/core/config`（`BaseAppSettings`，§2.2，比照 `monitoring_query_max_limit`） | — |
| `DEFAULT_SORT = "id:asc"` | `app/core/enums.py` 模組常數（與 `RecordSortField` 同檔，比照 `ADMIN_ROLE_RANK`） | — |
| `RecordService` | `app/services/record.py`（新檔） | — |
| `get_record_service` | `app/api/dependencies/services.py`（§2.6，加於 `get_user_service` 之後） | — |
| 單元測試 | `tests/unit/services/test_record.py`（§6） | — |

> model 層的檔案落點（models／repositories／migration／conftest fixture）見 model §10；api 層（router／schemas／註冊）見 api §12。
