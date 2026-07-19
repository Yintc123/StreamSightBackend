# 規格書（Model 層）：資料記錄（Record）管理 — 資料模型

> 狀態：**草案（尚未實作）** ／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「資料記錄管理」的 **Model 層**（資料表）。定義 `records` 與其分類查詢表 `record_categories` 的欄位／約束／索引／狀態機／不變式與 migration。業務規則（權限守衛、交易邊界、匯入）與 HTTP／DTO 屬 **companion 規格**（§0 非目標），另立。
>
> 🔗 **契約來源**：本表是前端 [StreamSightStreamlit `data-source.md`](../../../StreamSightStreamlit/docs/specs/data-source.md) 之 `Record` dataclass 的**真實持久化後端**。前端目前以 `MockDataSource`（記憶體）演示，未來 `ApiDataSource` 呼叫本後端 REST；本表須讓「頁面與行為測試零改動」地由 mock 換成 api（見前端 §「換成真實 API 時要改什麼」）。
>
> ⚠️ **命名對照**：前端 `Record`（`lib/models.py`）↔ 後端 `records` 表。前端 `created_by: str`（username）↔ 後端 `created_by_principal_id`（FK → admins.principal_id），username 於 API 層解析（§2.3）。

---

## 0. 功能總覽（先讀這裡）

**一句話**：把前端 `Record`（`id / title / value / category / created_by / created_at / updated_at / note / deleted_at`）落成一張**軟刪除 + 建立者稽核**的 `records` 表，支撐列表（分頁／分類篩選／標題關鍵字／排序）、單筆、建立、更新、軟刪除、批量匯入；編輯權以既有 RBAC 的 **AdminRole grade** 驅動（editor+ 可寫、viewer 唯讀，**非擁有權**，§2.9）。

**契約速查（前端 `DataSource` 方法 → 後端落點）**：

| 前端方法 | 語意 | 後端資料層需求 | 對應 HTTP（companion） |
|---|---|---|---|
| `list_records(page,size,category,keyword,sort,include_deleted)` | 分頁／篩選／排序，預設濾軟刪除 | 時間戳謂詞 + 分類/標題謂詞 + 穩定排序分頁 | `GET /records` |
| `get_record(id)` | 取單筆（不存在/軟刪→404） | `WHERE id=? AND deleted_at IS NULL` | `GET /records/{id}` |
| `create_record(data,actor)` | 建立，`created_by`/時間戳由來源指派 | INSERT，`created_by_principal_id=current` | `POST /records` |
| `update_record(id,data,actor)` | 需權限，更新 `updated_at` | UPDATE（可編輯欄位） | `PATCH /records/{id}` |
| `delete_record(id,actor)` | 需權限，軟刪除 | `SET deleted_at=now()` | `DELETE /records/{id}` |
| `bulk_create(rows,actor)` | 逐列驗證、上限 1000 | 批次 INSERT | `POST /records/bulk` |

> **可編輯欄位（前端只准填這四個）**：`title / value / category / note`。`id / created_by / created_at / updated_at / deleted_at` 一律由來源端管理，前端不得指定（前端 §資料契約）。

**例外 → HTTP（沿用前端契約，見前端 §例外）**：

| 後端例外（`app/core/exceptions`） | 觸發 | HTTP |
|---|---|---|
| `RecordNotFoundError` | get/update/delete 遇不存在或已軟刪除 id | 404 |
| `RecordPermissionError` | create/update/delete 由 grade < editor（如 viewer）發動 | 403 |
| `RecordValidationError` | 欄位不合法（title 空、category 名不存在／寫入用到 inactive、value 非數、sort 欄非法、size/page 非法） | 422 |

> ℹ️ **初始 admin 不再是特例**：初始/root admin 現為**真實 DB `admins` 列**（見 [`bootstrap-hidden-admin.md`](./bootstrap-hidden-admin.md)），其 `principal_id` 存在於 `admins` → `created_by_principal_id` FK 自然滿足。**故本表不再需要「擋初始 admin 寫入」的 `BusinessRuleError`**（早期草案因哨兵 `principal_id=0` 不落 DB 才需，現已根治）。

> **⚠️ 例外類別須繼承既有基類（對齊 codebase，非另建平行體系）**：本 codebase 只有**通用語意例外**（`app/core/exceptions/base.py`）：`NotFoundError`(404)、`ForbiddenError`(403)、`BadRequestError`(400)、`BusinessRuleError`(422)、`ConflictError`(409)…——**無 per-domain 子類先例、亦無 `ValidationError` 基類**。故本規格的 `Record*` 例外**若要新增，須明確繼承既有基類**以承接其 `status_code`／`error_code`（由全域 handler 統一映射）：
> - `RecordNotFoundError(NotFoundError)` → 404
> - `RecordPermissionError(ForbiddenError)` → 403
> - `RecordValidationError(BusinessRuleError)` → **422**（codebase 中 422 只由 `BusinessRuleError` 與 Pydantic/HTTPException 產生，別無 `ValidationError` 基類可繼承）
>
> **更簡單的退路**：若不想增生子類，直接**複用通用例外**（`NotFoundError`／`ForbiddenError`／`BusinessRuleError`）亦可——本 codebase 現行風格即如此。子類化的唯一好處是語意更清晰、可被上層精準 catch；屬 companion service/api 決策，本層只需保證觸發條件（欄位約束）就緒。

> **分類下拉來源**：分類值域改由 `record_categories` 查詢表提供（§2.4）。前端下拉選單經 `GET /records/categories`（companion api）取 `is_active=True` 的分類、依 `sort_order` 排序，不再硬編 `CATEGORIES`。
>
> **列表讀取路徑**（分類/關鍵字/排序/每頁筆數）全 server-side，最佳實踐見 §2.7；**權限模型**（列表全可見；編輯權純 grade-based——editor+ 可寫、viewer 唯讀，前端用 JWT grade、免 per-row 旗標）見 §2.9。

### 目標
- 給出 `records` 與分類查詢表 `record_categories` 的**權威資料模型**（欄位、型別、約束、索引、狀態機、不變式）。
- 忠實承接前端 `Record` 契約：`SORTABLE` 排序欄、軟刪除語意、可編輯欄位邊界；**分類值域改由 `record_categories` 表 + FK 保證**（供下拉動態來源，§2.4）。
- 定義 repository 的**列表查詢方法**（時間戳謂詞 + 篩選 + 分頁）與**啟用分類查詢**，屬讀取程式碼、無 DDL。
- 定義 append-only Alembic migration 建立 `record_categories`（含四筆種子）與 `records` 表。

### 非目標（Out of scope → companion 規格）
- **service／業務規則**（grade-based 寫入守衛 `require_min_admin_role(EDITOR)`、交易邊界、匯入逐列驗證與 1000 列上限）→ `records-service.md`（待立）。
- **HTTP／DTO／授權**（端點、`RecordSummary`、`created_by` username 解析、狀態碼）→ `records-api.md`（待立）。
- **授權機制**（`grade`、`require_min_admin_role`／`require_min_tier`）→ [`rbac.md`](./rbac.md)。
- 監控／時序資料：與本表無關（那走 Redis，見 [`monitoring.md`](./monitoring.md)）。

---

## 1. 背景與目標

前端資料管理頁已以 `MockDataSource`（40→200 筆記憶體種子）完成列表／分頁／篩選／排序／CRUD／匯入的互動與測試（前端 `data-source.md` §MockDataSource 行為規格）。mock **不持久化**、重啟即還原。本規格提供其真實後端：一張 `records` 表，讓 `ApiDataSource` 上線後前端頁面與行為測試零改動。

核心語意（承前端契約）：

1. **軟刪除**：刪除只設 `deleted_at`，列表預設排除；`include_deleted` 可帶出。
2. **建立者稽核**：每筆記錄記建立者（顯示與稽核用）；**編輯權純 grade-based**（editor+ 可寫、viewer 唯讀），與建立者無關（§2.9）。「誰最後改了什麼」以結構化 log 為權威來源（§2.8）。
3. **分類值域**：`category` 須為 `record_categories` 表中的分類（初始四筆 `感測器/系統/應用/網路`，對映前端 `CATEGORIES`）；值域由 FK 保證、下拉由該表動態提供（§2.4／§3.6）。
4. **排序白名單**：`sort` 欄 ∈ `{id, title, value, category, created_at}`（前端 `SORTABLE`），格式 `欄位:asc|desc`，預設 `id:asc`；非法欄位 → 422。

---

## 2. 設計決策

### 2.1 `records` 為獨立業務表，建立者 FK 掛 `admins`（DB 硬化「建立者必為 admin」）

records 為純 admin 功能（role=0 不可呼叫，§2.9），故**建立者恆為 admin**。`created_by_principal_id` 語意上屬**操作者稽核**欄。

**FK 目標選 `admins.principal_id`（unique 非 PK 欄）+ `RESTRICT` + `NOT NULL`**：

| 面向 | 本表 `created_by_principal_id` |
|---|---|
| FK 目標 | `admins.principal_id`（子表 unique 欄，**DB 直接證明必為 admin**） |
| ondelete | `RESTRICT`（建立者不可變、恆存；admin 幾乎只軟刪，見下） |
| nullable | `NOT NULL`（每筆恆有建立者，解析無 NULL 分支，§2.3） |

> **這與 codebase 既有慣例一致，非新奇 pattern**：現有 `admins`/`users` 對 `principals` 的 FK 就是 composite `(principal_id, role) → principals(id, role)`——指向的是**非 PK 的 composite unique constraint `uq_principals_id_role`**（見 `app/models/admin.py`／`user.py`）。故「FK 指向非 PK 唯一欄」本 codebase 早已在用；本表 FK → `admins.principal_id` 只是同一手法的單欄版本，**不是前所未有的選擇**。選它而非 `principals.id`，是因為 records 建立者恆為 admin 且不可變，用**更強**的 FK 讓 DB 直接保證「建立者必為 admin」（→ `principals.id` 會放進 role=0，與 §2.9 矛盾）。
>
> **與既有 audit-FK 慣例（`admins.archived_by/deleted_by`）的差異（誠實揭露）**：那對欄位是 FK → `principals.id` + `SET NULL` + nullable；本欄改用 `RESTRICT` + `NOT NULL`。差異在 **ondelete/nullable 策略**（本欄是不可變的建立者、恆存，非可空的稽核指標），非 FK 目標的 pattern 種類。
>
> **欄名保留 `*_principal_id`**：欄位存的仍是 principal_id（＝JWT `sub`，寫入時直接取用免查表），只是 FK 目標為 `admins.principal_id`。解析永遠 JOIN `admins`（§2.3／§4）。
>
> **若未來要開放 User 建立**（product 反轉）：改回 FK → `principals.id` + 復原 `users.name` 解析分支（§9）。本版依「role=0 不可用」明確設計，不預留。

> **✅ 初始 admin 已是真實 DB 列——不需 bootstrap 守衛（定案，§8）**：初始/root admin 現由 [`bootstrap-hidden-admin.md`](./bootstrap-hidden-admin.md) 於啟動時 upsert 成**一筆真實 `admins`（super_admin、`is_protected`）+ `principals` 列**（id 自增、非哨兵 0）。故其 `principal_id` **存在於 `admins`** → `created_by_principal_id=<真實 id>` FK（`RESTRICT`）**自然滿足**，寫 records 直接成功、`created_by`/`updated_by` 解析命中。
>
> - **早期草案的哨兵守衛已作廢**：先前因初始 admin 為 SSM 哨兵（`principal_id=0`、不落 DB）才需「service 擋初始 admin 寫入 → `BusinessRuleError`」；root 落 DB 後此守衛**無存在意義，移除**（記於 §8／§9-1a）。連帶消滅「單一 admin 部署 records 開箱不可用」的 UX 斷崖。
> - **同源修復**：`admins.archived_by/deleted_by` 對初始 admin 的同款 FK bug（C2）亦由同一設計根治（root 為真列 → `*_by=<真實 id>`）。

### 2.2 軟刪除用 `deleted_at`（單欄、終態），不引入 `deleted_by`

前端 `Record` 只有 `deleted_at`（無 `deleted_by`）。本表**對齊契約**：軟刪除＝`deleted_at IS NOT NULL`，`is_active` 語意由「`deleted_at IS NULL`」表示。

> **與 admin 表的差異（誠實揭露）**：`admins` 的封存／軟刪除是高稽核狀態轉移，各配 `*_by`（成對稽核）。`records` 為一般業務資料、量大、稽核需求低，**不加 `deleted_by`**（避免膨脹）；「誰刪的」以結構化 log 記。是否需 `deleted_by` 列為 Open Question（§9）。

### 2.3 建立者存 `created_by_principal_id`（FK），username 於 API 層解析

前端契約的 `created_by` 是**顯示字串**（username）。後端**存 principal_id**（穩定的建立者參照，稽核/顯示用；**不驅動授權**，§2.9），API 層再解析為顯示名——與 `admin-management-model.md` §4 把 `archived_by`(id) 解析成 `archived_by_username` 同手法。

- **解析規則**：建立者恆為 admin（§2.1／§2.9），故 `created_by` **一律 JOIN `admins` 取 `admins.username`**（無 `users.name` 分支——role=0 不可建立，該分支不存在）。API DTO 統一輸出 `created_by: str`。`created_by` 為**顯示/稽核**欄，**不決定授權**。
- **不可變**：`created_by_principal_id` 建立後不可改（`update_record` 不接受它，對齊前端「前端不得指定 created_by」）。
- **恆可解析（無 NULL 分支）**：所有 admin（含 root，§2.1）皆為真實 `admins` 列，`created_by` 存的必是真實 admin 的 `principal_id` → JOIN `admins` **必命中**、`created_by: str` 恆有值，無需 fallback 顯示名。

### 2.4 `category_id` 代理鍵 FK → `record_categories` 查詢表（供下拉選單動態來源）

> 🔄 **決策修訂（兩階段）**：
> 1. 原設計「`String(20)` + `CHECK IN(四值)` + StrEnum、**不建** lookup 表」→ 因**分類下拉需要可查詢、可維護的來源**（新增／改名／排序／停用免改碼重部署），改為建 `record_categories` 查詢表。`CHECK IN` 是編譯期固定集合，撐不起「後台管理分類、下拉即時反映」。
> 2. 曾一度採「`records.category` 存字串、自然鍵 FK → `record_categories.name`」→ **現改為代理鍵 `records.category_id` (int) FK → `record_categories.id`**（最佳實踐）。理由見下。

**為何用 `category_id`（代理鍵）而非存 `name`（自然鍵）**——關鍵是「分類既然是**被管理的實體**（可改名/停用），就該用穩定的代理鍵」：

| 面向 | `category_id`（採用） | 存 `name`（棄用） |
|---|---|---|
| 改分類名稱 | 改 `record_categories.name` 一列，records 全不動 ✅ | 需 `ON UPDATE CASCADE` 連改所有 records ❌ |
| 儲存/索引 | int（4B），index 小、join 快 ✅ | 字串每列重複約 20B ❌ |
| 正規化 | 分類值不在 records 重複 ✅ | 值重複於每列 ❌ |
| 顯示分類 | 需 join / API 解析 ⚠️ | 免 join 直讀 ✅ |

- **前端 `Record.category: str ∈ CATEGORIES` 契約不變**：`records` 內部存 `category_id`，但 **API 邊界維持字串**——回應時 join `record_categories.name` 輸出 `category: "感測器"`；建立/更新時把傳入的 `category` 字串解析成 `category_id`（同 `created_by_principal_id → username` 的解析模式，§2.3）。`ApiDataSource` 送/收字串一如既往、零改動。
- **原 `RecordCategory` StrEnum 取消**，改為 **ORM 模型 `RecordCategory`**（表 `record_categories`，§3.6）。四個初始分類由 migration seed（§6.2）——後端**唯一**的種子資料（records 本身不種）。
- **停用而非刪除**：退場分類設 `is_active=False`（不進下拉），保留列以維持既有 records 的 FK 完整性（`fk_records_category` 為 `RESTRICT`，§3.2）。
- **下拉來源**：`GET /records/categories`（companion api）回 `is_active=True`、依 `sort_order` 排序的分類清單。

> **排序副作用（重要）**：前端 `SORTABLE` 含 `category`。代理鍵下 `ORDER BY category_id` ≠ 依分類名排序（id 是插入序）。故 repository 遇 `sort=category:*` 時須 **join `record_categories` 並 `ORDER BY rc.name`**（對齊前端 mock 依字串排序的語意），見 §4／§2.7。

### 2.5 `title` 為明文 `String(200)`，關鍵字用 `LIKE`（不加密）

前端 `keyword` 對 `title` 做**不分大小寫子字串**比對。故 `title` 必須**明文可子字串查詢** → `String(200)`、`LOWER(title) LIKE :kw ESCAPE '\'`（`:kw` 已跳脫 `%`/`_`/`\`，見 §2.7-(1)）。

> **與 `users.email` 對比（設計要點）**：`email` 走 `DeterministicEncryptedString`（可等值/唯一，但**不能子字串**）。`records.title` 非 PII、需模糊搜尋，故**刻意不加密**。若未來 `title` 含敏感資訊，子字串搜尋需求與加密互斥，須另議（Open Question §9）。

### 2.6 `value` 用 `Float`（對齊前端 dataclass）

前端 `value: float`（種子 `round(uniform(10,999),1)`）＝量測值語意，`Float`（雙精度）即可。若日後需精確金額類，改 `Numeric(precision, scale)`——但那會改變契約型別，屬 Open Question（§9）。驗證（可轉數值）於 service 層（前端 `_validated_fields`）。

### 2.7 列表查詢（讀取路徑）最佳實踐：正規化 → 謂詞 → 計數 → 排序 → 分頁 → 解析

列表同時受四個 UI 控制（**分類 / 關鍵字 / 排序 / 每頁筆數**），全部 **server-side**（不抓全表回前端過濾）。**分層職責**：service 做「輸入正規化與驗證」（fail-closed），repository 做「純資料查詢」，以乾淨參數交界。

**(1) 輸入正規化與驗證（service 層）**
- **每頁筆數夾上限**：`size = min(max(size, 1), MAX_PAGE_SIZE)`（`MAX_PAGE_SIZE=100`）；`page = max(page, 1)`。**不信前端**——防 `size=10^9` 拖垮 DB／記憶體（§5）。
  > **⚠️ 與 router 層 `Query` bounds 的分工須明確（避免行為分裂）**：既有 admin router 以 `Query(50, ge=1, le=200)` **在進 service 前即由 FastAPI 拒絕越界**（回 422）。本規格改採 **service 內靜默夾值**（clamp，不拒絕）以對齊前端「送什麼都給頁」的 UX 契約。**兩者擇一、不可並存**——若 router 仍加 `Query(le=...)`，越界會先被 FastAPI 422 掉、永遠進不到 service 的夾值邏輯。**本規格定調：router 端 `size`/`page` 不設 `le` 上界（僅 `ge=1` 防負數），夾上限交 service**（companion api 落實）。
- **排序解析成 enum（唯一驗證點）**：把前端傳來的 `"field:dir"` 字串解析、驗證成 **`RecordSortField` enum + 方向**（`field ∈ RecordSortField` 否則 `RecordValidationError`；`dir ∈ {asc,desc}` 否則 422；空值套 `DEFAULT_SORT`）。**驗證只在此發生一次**——repo 收的是已型別化的 enum，不再 parse、不再拋 validation（§4）。**絕不**把使用者字串拼進 `ORDER BY`；欄名→ORM Column／JOIN 的映射屬查詢構造，留在 repo（比照 `AdminStatusFilter` 由 repo `_status_predicate` 翻 SQL 的既有形狀）。
- **關鍵字跳脫 LIKE 萬用字元**：對 `%` `_` `\` 前綴加跳脫字元、配 `ESCAPE '\'`；否則使用者輸入 `50%`／`a_b` 會誤配。空字串視同無關鍵字。
- **分類名 → id（篩選路徑允許 inactive）**：以 `get_by_name` 解析，**不檢查 `is_active`**——退場分類的舊資料仍要能被篩出（**與寫入路徑相反**，寫入要求 active，§2.4）。名稱完全不存在 → `RecordValidationError`（明確回饋，不靜默忽略打字錯）。

**(2) 謂詞（count 與 list 共用同一建構器，避免條件漂移）**
- `include_deleted=False` → `deleted_at IS NULL`（軟刪除預設隱藏；`is_active` 計算屬性不進 SQL，對齊 admin §2.7）。
- `category_id` 非 None → `category_id = :category_id`。
- `keyword` 非空 → `LOWER(title) LIKE :kw ESCAPE '\'`。

**(3) 計數**：以上謂詞（不含 order/limit）算 `COUNT(*)` → `Page.total`＝**篩選後、分頁前**筆數（前端據此算總頁數）。

**(4) 排序（穩定）**：`ORDER BY <col> <dir>, id <dir>`——**永遠補 `id` tie-breaker**，否則同值列在 OFFSET 分頁下跨頁跳動／重複／漏。`sort=category` 特例：join `record_categories` 依 `rc.name`（非 `category_id` 插入序，§2.4）。

**(5) 分頁**：`LIMIT :size OFFSET (page-1)*size`。**採 OFFSET** 而非 keyset——前端 `Page{total,page,size}` 需總數與任意跳頁 UX，OFFSET 在此資料量足夠。若日後深分頁（offset 上萬）成瓶頸，再評估 keyset（代價：失去精確 total 與跳頁，§9）。

**(6) 解析（一次 JOIN，避免 N+1）**：`category_id→name`、`created_by_principal_id→顯示名`，全部同一查詢以 JOIN 帶出（§4），**不逐列查**。

### 2.8 不新增 `updated_by`（對齊 admin §2.4；「誰改的」走結構化 log）

`records` **不加 `updated_by_principal_id` 欄**，與 [`admin-management-model.md`](./admin-management-model.md) §2.4「一般更新不加 `updated_by`、走結構化 log」**一致**：

- **前端契約無此欄**：前端 `Record` dataclass **無** `updated_by`，前端頁面不讀、不顯示。為一個**沒有任何消費者**的欄位增加 `欄位 + 索引 + FK + SET NULL 語意`，屬 YAGNI——先前草案曾以「records 高頻編輯、最後修改者常見於清單」為由加它，但**既然前端契約與 UI 都不需要，就不預先落 schema**。
- **「誰在何時改了什麼」以結構化 log 為權威來源**：`update_record` 由 service 記結構化 log（actor、record_id、前後值），追責完整且不膨脹業務表。`updated_at`（`onupdate` 自動刷新）記「何時」已足夠支撐前端顯示。
- **未來若真有 API/UI 要顯示「最後修改者」**：再以 append-only migration 加一欄即可（廉價、可逆），屆時解析同 `created_by`（JOIN `admins`）。列為 Open Question（§9）。

### 2.9 權限模型：可見性全開、編輯權純 grade-based（非擁有權）

**編輯權只看操作者的 AdminRole grade，與記錄的建立者無關**（**無**「只能編輯自己的」規則）：

| grade | 讀取（列表/單筆） | 新增 / 編輯 / 刪除 |
|---|---|---|
| `super_admin` / `editor` | ✅ | ✅ **全部資料** |
| `viewer` | ✅ | ❌ 唯讀 |

- **可見性（visibility）**：全部 active 記錄，任何 admin（viewer+）皆可見（列表**不加** owner 謂詞）。
- **可編輯性（editability）**：`grade ∈ {super_admin, editor}` → 可寫；`viewer` → 唯讀。**對所有列一致（uniform）、非 per-row**。

> ✅ **不需 per-row `can_edit` 旗標**：編輯權純 grade-based、對每一列都相同，**前端直接用 JWT 的 `grade`** 決定按鈕全域啟用/停用（viewer → 全部 disable；editor+ → 全部 enable）。先前需要 per-row 旗標的唯一理由（「user 限編輯自己」）已被移除，故旗標一併移除。`created_by` 退化為**純稽核/顯示**欄，**不參與授權**。
>
> ⚠️ **後端仍必擋（安全，非 UX）**：寫入端點一律 `require_min_admin_role(EDITOR)`；grade < editor（含 viewer）的 create/update/delete → 403（`RecordPermissionError`）。前端 disable 只是 UX，**絕不取代**後端授權（見 [`rbac.md`](./rbac.md)）。
>
> **範圍（scope）＝純 admin 功能**：所有 `/records*` 端點**僅 admin（role=1）可用**——讀取需 viewer+、寫入需 editor+，皆走 `require_min_admin_role(...)`（其本質已要求 role=1）。**一般 User（role=0）完全不能呼叫這些 API**（連讀都不行 → 403）。故**建立者恆為 admin**，這一事實由 model 層 FK 硬化（§2.1／§2.3）。

---

## 3. 資料模型

### 3.1 欄位

| 欄位 | 型別 | 約束 / 預設 | 說明 |
|---|---|---|---|
| `id` | `int` PK | Base 提供 | 自增主鍵（前端 `Record.id`） |
| `title` | `String(200)` | `NOT NULL`；`CHECK(char_length(title) > 0)` | 標題（必填非空、可子字串搜尋，§2.5） |
| `value` | `Float` | `NOT NULL` | 量測值（§2.6） |
| `category_id` | `int` | `NOT NULL`；`FK → record_categories.id`（`ON DELETE RESTRICT`）；`index` | 分類（§2.4；代理鍵；API 邊界解析回 `category` 字串） |
| `created_by_principal_id` | `int` | `NOT NULL`；`FK → admins.principal_id`（`ON DELETE RESTRICT`，§9-1）；`index` | 建立者（§2.1／2.3；不可變；恆為 admin；稽核/顯示用，**不決定授權**，§2.9） |
| `note` | `String(500)` | `NOT NULL`，`default ''`／`server_default ''` | 備註（可選，前端 `note=""`） |
| `deleted_at` | `DateTime(tz)` | nullable（`None`＝未刪除） | 軟刪除時間（§2.2；終態） |
| `created_at` / `updated_at` | `DateTime(tz)` | Base（`server_default now()`；`updated_at` `onupdate`） | 建立／最後更新時間 |

> `id` / `created_at` / `updated_at` 由 `Base`（`app/core/db/base.py`）提供，勿重複宣告。

### 3.2 約束（`__table_args__`）

- `ForeignKeyConstraint(["created_by_principal_id"], ["admins.principal_id"], ondelete="RESTRICT", name="fk_records_creator_admin")`（§2.1；DB 硬化「建立者必為 admin」，§9-1 討論 RESTRICT vs SET NULL）。

> **FK 目標為 `admins.principal_id`（unique 非 PK 欄）**：MariaDB／SQLite（測試 `create_all`）皆支援 FK 參照 unique 欄（同 codebase 既有 `(principal_id, role) → principals(id, role)` 之於 `uq_principals_id_role`，§2.1）。admins 只軟刪（`deleted_at`）、列永存，故 `RESTRICT` 不會被硬刪觸發。
- `CheckConstraint("char_length(title) > 0", name="ck_records_title_nonempty")`（title 非空，DB 兜底；service 亦驗，§2.5）。
- `ForeignKeyConstraint(["category_id"], ["record_categories.id"], ondelete="RESTRICT", name="fk_records_category")`（代理鍵 FK，§2.4；被參照分類不可硬刪，退場走 `is_active=False`）。

### 3.3 索引

| 索引 | 欄位 | 目的 |
|---|---|---|
| `ix_records_category_id` | `category_id` | 分類篩選／join（§2.4） |
| `ix_records_deleted_at` | `deleted_at` | 軟刪除謂詞（列表預設濾） |

> **不建 `created_by` 索引**：可見性全開（§2.9，列表不加 owner 謂詞），無「依建立者篩選」的查詢；`created_by` 顯示名解析走 `admins.principal_id`（admin 側已 unique index）的 JOIN，records 側無需再建索引。**規格內無讀取查詢可服務的索引不建**（純寫入放大成本，§5）。若日後 companion 引入「依建立者篩選/稽核清單」，再依實際查詢補 `ix_records_creator`。
>
> **關鍵字搜尋不建索引**：`LOWER(title) LIKE '%kw%'` 前綴不定，一般 B-tree 無效；demo 資料量小、可全表掃。若日後量大需全文檢索，另議（§9）。排序穩定性靠 `ORDER BY <field> <dir>, id <dir>`，`id` 為 PK 已有索引。

### 3.4 狀態機（資料層）

| 狀態 | 條件 | 列表預設可見 |
|---|---|---|
| active | `deleted_at IS NULL` | ✅ |
| deleted | `deleted_at IS NOT NULL`（終態、不可復原＊） | ❌（需 `include_deleted=True`） |

> ＊前端 mock 無「復原」操作（`delete_record` 為單向軟刪除）。是否提供 restore 屬 companion service 決策（Open Question §9）。

### 3.5 資料不變式

1. **`title` 非空**（CHECK；service 亦驗）。
2. **`category_id` 恆指向存在的分類**（FK → `record_categories.id`，§2.4；建立/更新時 service 另驗該分類 `is_active=True`）。
3. **`created_by_principal_id` 恆指向存在的 admin**（FK → `admins.principal_id`，`RESTRICT`；建立後不可變，§2.1／§2.3）。
4. **`value` 為數值**（型別；service 驗可轉 float，§2.6）。
5. **軟刪除單向**：`deleted_at` 一旦寫入即為終態（除非 companion 明確提供 restore，§9）。
6. **可編輯欄位邊界**：`update_record` 只改 `title/value/category/note`；`id/created_by/created_at/deleted_at` 不經 update 路徑改動（`updated_at` 由 `onupdate` 自動刷新）。

### 3.6 分類查詢表 `record_categories`（ORM 模型 `RecordCategory`）

下拉選單的權威來源（§2.4）。`records.category_id` 之 FK 目標（`id`）。

| 欄位 | 型別 | 約束 / 預設 | 說明 |
|---|---|---|---|
| `id` | `int` PK | Base 提供 | 代理主鍵（`records.category_id` 的 FK 目標；穩定、rename-safe） |
| `name` | `String(20)` | `NOT NULL`；`unique`；`index` | **分類值**＝前端 `CATEGORIES` 字串（如 "感測器"）；API 依此解析 `category` 字串、寫入時反解析成 `id` |
| `label` | `String(50)` | `NOT NULL` | 下拉**顯示文字**（種子時 = `name`；預留 i18n/改顯示名） |
| `sort_order` | `int` | `NOT NULL`，`default 0`／`server_default '0'` | 下拉排序（小→大） |
| `is_active` | `bool` | `NOT NULL`，`default True`／`server_default true` | 啟用中才進下拉；停用＝退場但保留列（§2.4） |
| `created_at` / `updated_at` | `DateTime(tz)` | Base | |

**約束（`__table_args__`）**：`UniqueConstraint("name", name="uq_record_categories_name")`（分類名唯一；供 API `name → id` 解析與寫入驗證的穩定查找鍵）。

**索引**：`name` 由 unique 帶索引（API 依 `name` 反查 `id`）；`id` 為 PK 自帶索引（records join 用）。

> **代理鍵的好處落地**：`records` 存 `category_id`（int），與 `name` 完全解耦——改 `name`（或 `label`）都只動 `record_categories` 一列、**不觸及任何 records**。`name` 仍 unique，是「分類的自然識別」，供 API 邊界 `category` 字串 ↔ `category_id` 雙向解析。
>
> **狀態機**：`is_active=True`（進下拉）⇄ `False`（退場、不進下拉但列保留、既有 records FK 仍有效）。**不提供硬刪**被參照的分類（FK `RESTRICT` 兜底）。

---

## 4. Repository 層增量（讀取程式碼，非 schema）

於 `app/repositories/record.py`（新檔，繼承 `BaseRepository`）提供（**無 DDL**）：

- `list_records(*, category_id, keyword, sort_field: RecordSortField, sort_dir: SortDirection, include_deleted, limit, offset) -> Sequence[Record]`：
  - 謂詞：`include_deleted=False` → `deleted_at IS NULL`；`category_id` 非 None → `category_id = :category_id`；`keyword` 非空 → `LOWER(title) LIKE :kw ESCAPE '\'`（`:kw` 已由 service 跳脫並包 `%…%`，§2.7-(1)——**repo 端勿漏 `ESCAPE '\'`**，否則 service 的跳脫失效）。
  - 排序（**收已驗證的 enum，不 parse、不拋 validation**）：以 module-level 映射把 `sort_field` 翻成 ORM Column（比照 `AdminRepository._status_predicate` 把 `AdminStatusFilter` 翻 SQL 的形狀，§2.7）；一般欄 `ORDER BY <col> <dir>, id <dir>`；**`sort_field is RecordSortField.CATEGORY` 特例**：`JOIN record_categories rc ON rc.id = records.category_id`，`ORDER BY rc.name <dir>, records.id <dir>`（依分類名而非 id 排序，§2.4／§2.7）。
  - 分頁：`LIMIT :size OFFSET (page-1)*size`（`size` 已由 service 夾在 `[1, MAX_PAGE_SIZE]`、`page≥1`，§2.7-(1)）。
  > **repo 只吃已正規化的乾淨參數**：size 夾上限、keyword 已跳脫（含 `ESCAPE '\'`）、**`sort` 已由 service 解析成 `RecordSortField` enum + 方向**（型別化、pyright 擋任意字串）、`category`（字串）已由 service 用 `get_by_name` 轉成 `category_id`——全在 service 完成（§2.7-(1)）。**驗證（含 `RecordValidationError`）不在 repo**：repo 收 enum 就不可能是非法欄名，故無需再驗。**篩選路徑的分類解析允許 inactive**（退場分類的舊資料仍可篩），僅寫入路徑要求 active。repo 純資料層、不碰字串語意。
- `count_records(*, category_id, keyword, include_deleted) -> int`：**與 `list_records` 共用同一謂詞建構器**（避免條件漂移）算 `COUNT(*)`（供 `Page.total`＝篩選後、分頁前筆數）。
- `get_active(record_id) -> Record | None`：`WHERE id=:id AND deleted_at IS NULL`（供 get/update/delete 前置；`None` → service 拋 `RecordNotFoundError`）。
- `get_active_row(record_id) -> RecordListRow | None`：同 `get_active` 謂詞 + 與 `list_records` **同一套 JOIN 解析**的單筆版——供 service 統一組單筆回應（create/update/get 皆走此，service §3.3）。與 list 共用同一 select 建構器（僅多 `WHERE id=:id`）。
- **`RecordListRow`（`@dataclass(frozen=True)`，定義於本檔 `app/repositories/record.py`，比照 `AdminListRow` 之於 `admin.py`）**：`record: Record` + `category_name: str`（JOIN `record_categories.name`）+ `created_by_username: str`（JOIN `admins.username`，恆命中，§2.3）——list/get 的一列，解析結果一次帶出（免 N+1，§2.7-(6)）。
- `bulk_insert(records: list[Record]) -> None`：批次 INSERT（companion service 逐列驗證後呼叫；上限 1000 由 service 把關）。

於 `app/repositories/record_category.py`（新檔，繼承 `BaseRepository`）：

- `list_active(*, order_by_sort=True) -> Sequence[RecordCategory]`：`WHERE is_active = true`，`ORDER BY sort_order, name`（供 `GET /records/categories` 下拉來源）。
- `list_all() -> Sequence[RecordCategory]`：不濾 `is_active`（供後台分類管理，若 companion 提供）。
- `get_by_name(name) -> RecordCategory | None`：`name → 分類列`（**純存在查找、不判 active**）。呼叫端決定語意：**寫入路徑**若 `None` 或 `is_active=False` → `RecordValidationError`（不得用退場分類建資料）；**篩選路徑**只需存在即可（允許 inactive，§2.7-(1)）。`None`（名稱不存在）兩路徑皆 → `RecordValidationError`。

> **API 邊界的兩處解析（供 `RecordSummary`）**：
> 1. **`category_id → category`（字串）**：`list_records`／單筆查詢 `JOIN record_categories rc` 帶出 `rc.name`，API 輸出 `category: "感測器"`，維持前端契約（§2.4）。
> 2. **`created_by_principal_id → created_by`（username）**：`JOIN admins ON admins.principal_id = records.created_by_principal_id` 取 `admins.username`（恆為 admin，無 users 分支，§2.1）。同 [`admin-management-model.md`](./admin-management-model.md) §4 手法。
>
> 裸 id 欄（`category_id` / `created_by_principal_id`）保留作穩定參照；列表與單筆共用同一套解析（DRY）。屬 companion api 決策，本層只需保證欄位、FK、join 目標就緒。

> **分類值域已改為資料表**（§2.4）：不再有 `RecordCategory` StrEnum；分類由 `record_categories` 表（+ FK）保證，執行期以 `list_active` 讀取（下拉）、`get_by_name` 解析/驗證（寫入）。
>
> **跨層詞彙與常數**（`app/core/enums.py`／`config`）：
> - **`RecordSortField(StrEnum)`** — 可排序欄位的封閉白名單，值 `id/title/value/category/created_at` **對映前端 `SORTABLE`**（契約不變）。採 `StrEnum` 對齊既有 `AdminStatusFilter`/`AdminRole` 慣例——供 service 驗證（`RecordSortField(field_str)` 非法即 `ValueError`→`RecordValidationError`）、repo 型別化收參（pyright 擋任意字串）。**取代原本鬆散的 `RECORD_SORTABLE` set**。
> - **`SortDirection(StrEnum)`** — `asc`/`desc`（同上，型別化方向）。
> - `DEFAULT_PAGE_SIZE=20`、`MAX_PAGE_SIZE=100`（每頁筆數夾上限，§2.7-(1)／§5）；`DEFAULT_SORT="id:asc"`（service 於 `sort` 空值時套用）。
>   > 註：本 codebase 現無全域分頁常數（admin router 以 `Query(50, ge=1, le=200)` 就地界定）。本規格**新增** `MAX_PAGE_SIZE`/`DEFAULT_PAGE_SIZE`/`DEFAULT_SORT` 至 config，並把夾值移到 service（§2.7-(1) 已說明與 router `Query` bounds 的分工）。
>
> **`RecordSortField` 是「欄位名」而非「分類值」**：其成員是 schema 的可排序欄（含名為 `category` 的欄），與 `record_categories` 表存的分類**資料**（感測器/系統…）正交、不重疊——前者由 schema+契約決定（改欄＝改碼），後者執行期由後台管理，故一個用 enum、一個用表（§2.4 同判準），無同步問題。

---

## 5. 安全性考量（資料層）

- **建立者完整性（稽核 + 型別硬化）**：`created_by_principal_id` FK → `admins.principal_id`（`RESTRICT`）→ ①DB 保證建立者**必為 admin**（integrity-first，堵住 role=0 混入，§2.1）；②不能硬刪仍有記錄的 admin（admin 幾乎只軟刪）；③建立者不可變＝稽核完整性（非授權考量，§2.9）。
- **授權：grade-based，後端 enforce**：編輯權＝ `grade ∈ {super_admin, editor}`；寫入端點一律 `require_min_admin_role(EDITOR)`、讀取需 viewer+（走 [`rbac.md`](./rbac.md) 的 `grade`／`require_min_admin_role`）。前端用 JWT grade disable 按鈕僅為 **UX**，**絕不取代**後端授權。**與擁有者無關**（§2.9）。
- **`title` 明文**：非 PII、為支援子字串搜尋刻意不加密（§2.5）；若誤存敏感內容為使用風險，須於 companion／使用規範約束。
- **注入防護**：`keyword` 一律走參數綁定（`LIKE :kw`），不字串拼接；**排序欄映射到 ORM Column**（白名單），使用者字串不進 `ORDER BY`（§2.7-(1)）。
- **LIKE 萬用字元跳脫**：`keyword` 的 `%`/`_`/`\` 須跳脫並配 `ESCAPE '\'`，否則 `50%` 之類輸入誤配（正確性 + 避免慢查詢，§2.7-(1)）。
- **每頁筆數上限（DoS 防護）**：`size` 夾在 `[1, MAX_PAGE_SIZE=100]`——不信前端，防 `size=10^9` 拉爆 DB／記憶體（§2.7-(1)）。
- **可見性 vs 可編輯**：列表全部可見（viewer+）、編輯權純 grade-based（editor+，§2.9）；建立者不參與授權。
- **稽核完整性**：「誰在何時改了什麼值」以結構化 log 為權威來源（§2.8，不另立 `updated_by` 欄）；`created_by` 提供「建立者」的快速顯示與稽核參照。
- **分類完整性**：`records.category_id` FK `RESTRICT` → 被引用的分類不可硬刪，退場只設 `is_active=False`（§2.4），杜絕「刪分類導致既有 records 懸空」；寫入時 service 另擋 `is_active=False` 的分類（避免用已退場分類建新資料）。

---

## 6. 資料模型的 Migration

### 6.1 新 revision（append-only，接於現行 head `e5f6a7b8c9d0` 之後）

> **`down_revision` 以實作當下的實際 head 為準**：本規格撰寫時 `alembic heads` 為單一 head `e5f6a7b8c9d0`（`add_admin_is_protected`），故新 revision 應 `down_revision = "e5f6a7b8c9d0"`。**動手前務必再跑一次 `uv run alembic heads` 確認**——若期間已有新 migration 併入，head 會前移；接錯會造成 multiple heads、`alembic upgrade head` 報錯而需手動 merge。

**建立順序有意義**（FK 目標須先存在）：先建 `record_categories` 並種入四筆，再建 `records`（其 `category_id` FK 指向前者 `id`）。

`upgrade()`：
1. `CREATE TABLE record_categories`（§3.6；`name` unique）。
2. **Seed 四筆初始分類**（`op.bulk_insert`）：`感測器 / 系統 / 應用 / 網路`，`label = name`，`sort_order = 0,1,2,3`，`is_active = True`。對映前端 `CATEGORIES` 順序（其 `id` 由自增產生，records 以 `category_id` 參照）。
3. `CREATE TABLE records`（欄位見 §3.1；`id` PK、`created_at`/`updated_at` server_default）。
4. FK：`fk_records_creator_admin`（→ `admins.principal_id`，`RESTRICT`）、`fk_records_category`（→ `record_categories.id`，`RESTRICT`）。
5. CHECK：`ck_records_title_nonempty`。
6. 兩個 index：`ix_records_category_id`、`ix_records_deleted_at`。

`downgrade()`：反序 `DROP TABLE records` → `DROP TABLE record_categories`（先 records，因其 FK 依賴 categories）。

> **可攜性**：`char_length()`、int FK 於 MariaDB／SQLite（測試 `create_all`）皆可；CHECK/FK 直接反映。真 MariaDB 驗 upgrade/downgrade。
>
> **種子分工**：`record_categories` 的四筆是**系統必需的參考資料**（無它 records 無法建立），故由 migration seed。`records` 本身**不種** demo 資料（那是前端 mock 職責）——空表上線、資料由 API 建立。
>
> **測試環境**：SQLite 測試走 `create_all`（不經 migration），故四筆分類種子須由 conftest fixture 注入（新增 `record_categories` fixture，種入四分類），供 records 相關測試建立資料時滿足 FK。

---

## 7. TDD 測試計畫（資料層；先寫、先看到 RED）

依 `CLAUDE.md` Red→Green→Refactor，每項先寫失敗測試：

### 7.1 Unit — `record_categories` model / DB（`tests/unit/test_record_category_model.py`）
- `uq_record_categories_name`：重複 `name` → `IntegrityError`。
- `is_active` 預設 `True`、`sort_order` 預設 `0`（server_default）。

### 7.2 Unit — `records` model / DB（`tests/unit/test_record_model.py`）
- `fk_records_category`：`category_id` 不存在於 `record_categories` → `IntegrityError`；有效 `category_id` → 可寫入。（**注意**：測試需先種四分類、取其 `id`，見 §6 fixture。）
- `ck_records_title_nonempty`：`title=''` → `IntegrityError`；非空 → 可寫入。
- `fk_records_creator_admin`：`created_by_principal_id` 指向**非 admin 的 principal（如 role=0 user）或不存在** → `IntegrityError`（硬化「建立者必為 admin」，§2.1）；指向存在 admin → 可寫入。
- `note` 預設 `''`；`deleted_at` 預設 `NULL`。
- `updated_at` 於 UPDATE 後自動刷新（`onupdate`）。

### 7.3 Unit — Repository
`tests/unit/repositories/test_record.py`：
- `list_records`：預設濾 `deleted_at IS NULL`；`include_deleted=True` 帶出軟刪除列。
- 篩選：`category_id` 精確；`keyword` 對 `title` 不分大小寫子字串。
- **篩選 inactive 分類**：以某 `is_active=False` 分類的 `id` 篩選，仍回其 records（repo 不判 active，§2.7-(1)）。
- **LIKE ESCAPE**：以已跳脫的 `\%` 當關鍵字 → 只配「字面含 `%`」的 title，不配任意字元（驗 `ESCAPE '\'` 生效，§2.7-(1)）。
- 排序：各 `RecordSortField` 成員（對映前端 `SORTABLE`）asc/desc 正確；同值列以 `id` 穩定次序。（repo 收 enum、不驗欄名——非法欄名的拒絕測試屬 service，見 §7.4。）
- **`sort=category`**：依 `record_categories.name` 排序（非 `category_id`）——建構不同 `name`／`sort_order` 的分類，斷言結果依 `name` 而非插入序（§2.4）。
- 分頁：`limit/offset` 切片正確；`count_records` 與對應 `list` 筆數一致（篩選後、分頁前，**共用謂詞**）；page 超出末頁回空 items 且 total 不變。
- `get_active`：軟刪除列回 `None`。

`tests/unit/repositories/test_record_category.py`：
- `list_active`：排除 `is_active=False`，`ORDER BY sort_order, name`。
- `get_by_name`：命中回列（含 `is_active` 值）、不存在回 `None`。

### 7.4 Service 正規化（companion `records-service.md`，先列此供追蹤）
讀取路徑最佳實踐（§2.7-(1)）的驗證點——正式測試隨 service 規格交付，但先在此鎖定：
- **size 夾上限**：`size>MAX_PAGE_SIZE` → 夾 100；`size<1`/`page<1` → 夾 1。
- **sort 解析成 enum**：`"field:dir"` → `RecordSortField` + `SortDirection`；非法欄名／方向 → `RecordValidationError`（唯一驗證點）；空值套 `DEFAULT_SORT`。欄名→Column／JOIN 映射屬 repo（收 enum），不在此測。
- **keyword 跳脫**：含 `%`/`_`/`\` 的關鍵字被當**字面**比對（非萬用字元）。
- **category 解析分流**：名稱不存在 → 422；篩選路徑接受 inactive；**寫入路徑**（create/update）拒 inactive → 422。
- ~~bootstrap 守衛~~ → **已移除**：初始 admin 現為真實 DB 列（§2.1／`bootstrap-hidden-admin.md`），`created_by` FK 自然滿足，無需哨兵守衛與其測試。

> **service／API 的其餘 TDD**（grade-based 寫入守衛：viewer 寫入 → 403、editor+ 可寫；匯入 1000 列上限與逐列 errors、username 解析、狀態碼）於 companion 規格交付；integration 測試（`httpx` ASGI client 打 `/records*`）屬 API 層。

---

## 8. 已定案決策（草案）

- ✅ **records 為純 admin 功能**（§2.9）：`/records*` 僅 admin（role=1）可用——讀取 viewer+、寫入 editor+；**User（role=0）完全不可呼叫**（含讀取 → 403）。
- ✅ **初始 admin 為真實 DB 列 → 無需 bootstrap 守衛**（§2.1／§9-1a）：root 由 [`bootstrap-hidden-admin.md`](./bootstrap-hidden-admin.md) 啟動時 upsert 成真實 `admins`（super_admin、`is_protected`、id 自增）→ `created_by` FK 自然滿足。**早期「擋初始 admin 寫入」的哨兵守衛已作廢移除**，連帶消除「單一 admin 部署 records 開箱不可用」的斷崖。`created_by`/`updated_by` 恆為真實 admin、解析恆命中。
- ✅ **建立者 FK 釘 `admins.principal_id`（`RESTRICT` + `NOT NULL`）**（§2.1）：讓 **DB 直接證明「建立者必為 admin」**（→ `principals.id` 會放進 role=0，與 §2.9 矛盾）。此為 codebase **既有** FK-to-non-PK-unique 手法的單欄版本（現有 `(principal_id, role)→principals(id,role)` 即指向 `uq_principals_id_role`），**非新奇 pattern**；與 audit 先例 `archived_by/deleted_by` 的差異僅在 ondelete/nullable（本欄不可變、恆存 → `RESTRICT`/`NOT NULL`）。解析手法（JOIN admins → username）與 admin §4 同構。`created_by` 不可變、恆解析為 `admins.username`（無 users 分支），且**不決定授權**。
- ✅ **不新增 `updated_by`**（§2.8）：對齊 admin §2.4——前端契約無此欄、無 UI 消費者，不預先落 schema（避免欄位+索引+FK 的膨脹）；「誰改的」走結構化 log，「何時」由 `updated_at`（`onupdate`）記。未來真有需求再以 append-only migration 加（§9）。
- ✅ 軟刪除單欄 `deleted_at`、無 `deleted_by`（對齊前端契約、避免膨脹，§2.2）。
- 🔄 **`category` 改為 `record_categories` 查詢表 + 代理鍵 FK**（供下拉動態來源）——**推翻原「CHECK IN + StrEnum、不建 lookup 表」決定**（§2.4）。最佳實踐採 **`records.category_id` (int) FK → `record_categories.id`**（rename-safe、正規化、index 小）；前端 `category: str` 契約由 **API 邊界 `category_id ↔ name` 解析**維持不變。`name` 仍 unique。退場走 `is_active=False`，四分類由 migration seed。排序 `category` 依 `name`（§2.7）。
- ✅ `title` 明文 `String(200)` 以支援不分大小寫子字串搜尋，刻意不加密（§2.5）。
- ✅ `value` `Float` 對齊前端 dataclass（§2.6）。
- ✅ **列表讀取路徑最佳實踐**（§2.7）：server-side 分類/關鍵字/排序/分頁；service 正規化（size 夾 `[1,100]`、page≥1、**sort 解析成 `RecordSortField` enum（唯一驗證點）**、**keyword LIKE 跳脫 + ESCAPE**、category 名→id）＋ repo 純查詢（收 enum、翻 Column/JOIN，比照 `AdminStatusFilter`+`_status_predicate`）；count/list **共用謂詞**；`ORDER BY … , id` **穩定分頁**；`sort=category` 依 `name`；解析走**單次 JOIN**（免 N+1）；採 **OFFSET 分頁**（契合 `Page{total,page}`）；夾值在 service、router 不設 `le` 上界（避免 FastAPI 先 422，§2.7-(1)）。
- ✅ **權限模型：可見性全開 + 編輯權純 grade-based**（§2.9）：全部 active 記錄 viewer+ 可見；`editor`/`super_admin` 可全 CRUD、`viewer` 唯讀，**與擁有權無關**。**不需 per-row `can_edit` 旗標**——前端用 JWT grade 全域決定按鈕；後端寫入端點 `require_min_admin_role(EDITOR)` enforce。`created_by` 為純稽核/顯示。
- ✅ **篩選 vs 寫入的分類解析分流**（§2.7-(1)／§4）：篩選允許 inactive 分類、寫入要求 active。
- ✅ **例外繼承既有基類**（§0）：`Record*` 例外若新增須繼承 `NotFoundError`/`ForbiddenError`/`BusinessRuleError`（承 status_code）；或直接複用通用例外（codebase 現行風格）。屬 companion service/api 決策。

## 9. 待確認事項（Open Questions）

1. **FK ON DELETE：`RESTRICT` vs `SET NULL`**（`created_by → admins.principal_id`）？本草案取 `RESTRICT`（建立者不可變、admin 幾乎只軟刪，稽核完整性）；若需「刪 admin 帳號保留其記錄」則改 `SET NULL` + `created_by_principal_id` 改 nullable（顯示名會變 None）。注：`created_by` 不決定授權（§2.9），此純屬稽核欄取捨。
1a. ~~**哨兵 `principal_id=0`（初始 admin）如何處理 `created_by` FK？**~~ → **已根治（[`bootstrap-hidden-admin.md`](./bootstrap-hidden-admin.md)）**：初始 admin 改為啟動時 upsert 成**真實 `admins`+`principals` 列（id 自增）**，消滅哨兵特例。`created_by` FK→`admins.principal_id` 自然滿足，**bootstrap 守衛移除**；同一設計順帶修掉 `admins.archived_by/deleted_by` 的哨兵 FK bug（C2）。維持 FK `NOT NULL` + `RESTRICT` + →`admins.principal_id` 不變。
2. **是否加 `deleted_by`**（稽核「誰刪的」）？本草案否（對齊前端契約）；若後台需追責再議。
2a. **是否加 `updated_by`**（稽核「最後修改者」，§2.8）？本草案否（前端契約無、無 UI 消費者、對齊 admin §2.4）；若日後清單/稽核需顯示「最後修改者」，再以 append-only migration 加一欄（解析同 `created_by`）。
3. **是否提供 restore（軟刪除復原）**？前端 mock 為單向刪除；companion service 決策。
4. **`value` 是否需精確 `Numeric`**（金額/計量場景）？會改變前端契約型別。
5. **`title` 全文檢索**：資料量大時 `LIKE '%kw%'`（前綴不定、無法用索引）退化，是否引入 MariaDB FULLTEXT／外部檢索？
5b. **深分頁效能**：OFFSET 在 offset 上萬時變慢；是否在特定清單改 keyset/cursor 分頁（代價：失去精確 total 與任意跳頁，與現有 `Page{total,page}` 契約相斥）。
5c. **`MAX_PAGE_SIZE=100` 數值**：是否符合實際清單需求（可調 config）。
5d. ~~row-level 可見性~~ → **已定案：全部可見、只有編輯受限**（不做 row-level 過濾，§2.9／§8）。
6. **匯入去重**：前端 mock 階段不去重（`data-source.md` §匯入）；後端是否需唯一鍵？
7. ~~分類 FK 自然鍵 vs 代理鍵~~ → **已定案採代理鍵 `category_id`**（§2.4／§8）。
8. **分類管理 CRUD**（新增／改 `name`／改 `label`／改排序／停用）：屬後台功能，端點與授權由 companion `records-api.md` 定；代理鍵下改 `name`/`label` **不觸及 records**（rename-safe）。本層只保證表結構與 `list_active`／`get_by_name` 就緒。

---

## 10. 實作接線清單（檔案落點與註冊）

| 產物 | 檔案 | 註冊 / 匯出 |
|---|---|---|
| ORM `Record` | `app/models/record.py`（新檔） | ⚠️ **必加進 `app/models/__init__.py`**（import + `__all__`） |
| ORM `RecordCategory` | `app/models/record_category.py`（新檔） | 同上 |
| `RecordRepository` + `RecordListRow` | `app/repositories/record.py`（新檔，§4） | — |
| `RecordCategoryRepository` | `app/repositories/record_category.py`（新檔，§4） | — |
| Migration | `alembic/versions/`（新 revision，§6.1） | `down_revision`＝實作當下 head |
| 四分類 fixture | `tests/conftest.py`（§6 測試環境） | 供 records 測試滿足 FK |

> ⚠️ **漏註冊 `app/models/__init__.py` 的症狀（先講明，防鬼打牆）**：測試 DB 走 `Base.metadata.create_all`（不經 migration）——模型未被 import 進 metadata 就**不會建表**（測試報 `no such table`），alembic autogenerate 也偵測不到新表。**第一個 RED 測試之前先註冊兩個模型。**
