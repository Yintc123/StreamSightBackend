# 規格書：型別內權限等級（方案 A — 等級 enum）

> 狀態：Draft ／ 目標版本：next+1 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 關鍵設計決策與取捨（為什麼這樣設計）另記於 [`../decisions/rbac.md`](../decisions/rbac.md)。本文聚焦「怎麼做」。
>
> 🔗 依賴 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md)（principals supertype、`role` 型別判別子、`grade` claim 掛靠其 JWT 機制）。本規格接手該規格 §11 Open Q3「未來 RBAC 另立規格」。

---

## 1. 背景與目標

`jwt-role-and-admin.md` 引入的 `principals.role`（0=user / 1=admin）是**型別判別子**（不可變、決定走哪張 child 表），**不是權限**。本規格在型別**之內**再加一層「權限等級」：

- **Admin 側**：CMS 管理者分三級 `SUPER_ADMIN / EDITOR / VIEWER`（權限高→低的階梯）。
- **User 側**：一般使用者分級 `FREE / PREMIUM`（示範值；實際等級待確認，見 §11）。

採**方案 A（等級 enum）**——每種身分一個獨立的等級 enum，DB 欄位硬化、以有序階梯做授權；**不引入** permissions/roles 關聯表（那是方案 B，另待需求成熟再議，見決策 R1）。

### 目標

- `UserTier`（user 側）／`AdminRole`（admin 側）兩個 **`StrEnum`**；admin 側附權限**排序**。
- `users.user_tier`、`admins.admin_role` 各一欄（DB `CHECK` 值域硬化；預設**最低權限** fail-safe）。
- JWT 新增 **`grade` claim**（該型別內等級的字串值），供前端渲染 UI；後端授權仍讀 child 現值。
- 授權 dependency：`require_min_admin_role(...)`（階梯）、`require_min_tier(...)`；`/me` 曝露等級供前端。
- seed 初始 admin 佈為 `SUPER_ADMIN`。

### 非目標（Out of scope）

- 細粒度 permission / scope 關聯表（方案 B）。
- admin 管理 API（建立/升降權其他 admin）——另立規格；本規格只加「等級欄 + 授權機制 + 讀取路徑」。
- 計費/訂閱流程（`user_tier` 如何變動的商業邏輯）。

---

## 2. 設計決策（摘要，詳見 [`../decisions/rbac.md`](../decisions/rbac.md)）

- **R1**：採方案 A（等級 enum），非方案 B（permissions 表）——YAGNI，等能力數膨脹再升級。
- **R2**：權限層與型別判別子**分離**——`principals.role` 不動、不重用；等級另存 child 欄。
- **R3**：`grade` claim 用 **`StrEnum` 字串**（前端可讀、自我描述）；claim key **避開 `role`**（已被型別佔用）。
- **R4**：`AdminRole` 為**有序階梯** + `require_min_admin_role`；非嚴格階梯時才升方案 B。
- **R5**：claim 僅為 **UX 提示**，非授權邊界；前端 UI 真實來源＝`/me`；權限可變的陳舊由 refresh 自然刷新 + 敏感操作讀 child 現值處理。
- **R6**：fail-safe 預設**最低權限**（`VIEWER` / `FREE`）；seed admin 例外給 `SUPER_ADMIN`。

---

## 3. 資料模型

### 3.1 Enum（`app/core/enums.py`）

型別判別子 `Role` 是 `IntEnum`（對外整數）；本層等級是**給前端讀的字串**，用 `StrEnum`（對齊既有 `AppEnv`/`LogLevel`）：

```python
from enum import StrEnum

class UserTier(StrEnum):
    FREE = "free"
    PREMIUM = "premium"

class AdminRole(StrEnum):
    SUPER_ADMIN = "super_admin"  # 全權，含管理其他 admin
    EDITOR    = "editor"     # 日常 CMS 讀寫，不含管理 admin
    VIEWER      = "viewer"       # 唯讀

# 權限高→低（值越大權限越高）；供 require_min_* 階梯比較
ADMIN_ROLE_RANK: dict[AdminRole, int] = {
    AdminRole.SUPER_ADMIN: 2,
    AdminRole.EDITOR:     1,
    AdminRole.VIEWER:       0,
}
USER_TIER_RANK: dict[UserTier, int] = {
    UserTier.FREE:    0,
    UserTier.PREMIUM: 1,
}
```

> **命名刻意避開 `USER` / `ADMIN`**：`Role.USER` / `Role.ADMIN` 已是型別判別子；admin 等級用 `SUPER_ADMIN / EDITOR / VIEWER`，避免 `AdminRole.USER` 與 `Role.USER` 在程式與 JWT 中混淆（見決策 R3）。

### 3.2 Child 欄位（各自一欄，DB CHECK 硬化）

- `User`（改）：新增 `user_tier`，**預設 `FREE`**（最低）：
  ```python
  user_tier: Mapped[str] = mapped_column(
      String(20), default=UserTier.FREE.value, server_default=UserTier.FREE.value
  )
  __table_args__ = (
      # ...既有約束...
      CheckConstraint(
          "user_tier IN ('free','premium')", name="ck_users_user_tier"
      ),
  )
  ```
- `Admin`（改）：新增 `admin_role`，**預設 `VIEWER`**（最低權限 fail-safe）：
  ```python
  admin_role: Mapped[str] = mapped_column(
      String(20), default=AdminRole.VIEWER.value, server_default=AdminRole.VIEWER.value
  )
  __table_args__ = (
      # ...既有約束（複合 FK、CHECK(role=1)）...
      CheckConstraint(
          "admin_role IN ('super_admin','editor','viewer')",
          name="ck_admins_admin_role",
      ),
  )
  ```

> **⚠️ `admins` 會有兩個「role」相關欄位，用途完全不同、勿混淆**（實作時各加註解）：
> - `role`（`SmallInteger`，**常數 1**）＝ jwt-role 的**型別判別子**，不可變、被複合 FK + `CHECK(role=1)` 釘死、決定「這是 admin」。**不進 DTO、不對外**。
> - `admin_role`（`String`，`super_admin`/`editor`/`viewer`）＝ 本規格的**權限等級**，可變、供授權與 `grade` claim。
>
> 兩者是「型別 vs 權限」兩個正交軸（見決策 R2）。`users` 同理有 `role`(常數 0) 與 `user_tier`，但名稱夠不同、較無混淆。
>
> **存字串值、程式端以 enum 包裝**（比照 jwt-role 規格 `Role` 存 `SmallInteger`＋`CHECK`）：讀 `AdminRole(admin.admin_role)`、寫 `admin.admin_role = AdminRole.EDITOR.value`。等級是 child **本地欄位**，`get_current_admin` / `get_current_user` 讀它**免 join、免 async footgun**（同 `is_active`，見 jwt-role D4b）。
>
> 亦可改用 `Enum(AdminRole, native_enum=False, length=20)`（SQLAlchemy 在非原生方言自動產 VARCHAR + CHECK）；本規格採「顯式 `String` + 手寫 `CheckConstraint`」以貼齊此 codebase 既有風格。

### 3.3 Migration（新 revision，接 jwt-role-and-admin 的最後一支）

1. `users` `ADD COLUMN user_tier VARCHAR(20) NOT NULL DEFAULT 'free'` + `CHECK(user_tier IN ('free','premium'))`。既有列由 default 自動填 `FREE`（無需另行回填）。
2. `admins` `ADD COLUMN admin_role VARCHAR(20) NOT NULL DEFAULT 'viewer'` + `CHECK(admin_role IN ('super_admin','editor','viewer'))`。
3. **既有 seed admin 升為 `SUPER_ADMIN`**：`UPDATE admins SET admin_role='super_admin' WHERE email = :initial_admin_email`（或改由 seed script 冪等設定，見 §5.6）。否則全體 admin 都停在 `VIEWER`、無人能升權。
4. down：對稱 drop 兩欄與兩個 CHECK。

> 測試走 SQLite `create_all`（不經 migration）；CHECK 需 SQLite 實際強制（本專案 conftest 已具備）。產出後人工檢視、真 MariaDB 驗 upgrade/downgrade。

---

## 4. JWT `grade` claim（`app/core/auth/jwt.py`）

沿用 jwt-role 規格的 `create_access_token`，再加 `grade`：

- `create_access_token(subject, role: Role = Role.USER, grade: str | None = None) -> str`：payload 加 `"grade": grade`（`None` 則不放此 key，向後相容）。
- `extract_grade(payload) -> str | None`：`payload.get("grade")`（缺 → `None`）。

一個 editor admin 的 payload：
```jsonc
{ "sub": "7", "role": 1, "grade": "editor", "type": "access", "iat": ..., "exp": ... }
```

> **📌 `grade` 即該身分的等級欄，前端可據此得知權限**：
> - `role == 1`（admin）→ **`grade` == `admins.admin_role`**（`"super_admin"` / `"editor"` / `"viewer"`）。前端解 token 讀 `grade` 即知該 admin 的權限等級。
> - `role == 0`（user）→ **`grade` == `users.user_tier`**（`"free"` / `"premium"`）。
>
> 即：`grade` 值域由 `role` 決定。因 user/admin 為兩個分離前端，各情境下 `grade` 語意單一，故**單一 claim** 即可，不拆 `user_tier`/`admin_role` 兩個（見決策 R3）。
>
> ⚠️ 前端讀 `grade` 僅為 **UX 提示**（首屏零往返粗畫），**非授權邊界、且可能陳舊**；權威狀態以 `/me` 為準、授權由後端讀 child 現值判定（見 §5.5、§7、決策 R5）。

---

## 5. 介面設計

### 5.1 Service — 簽發時帶入 grade

- `login` / `admin_login`：驗證後讀 child 的等級 → `create_access_token(principal_id, role, grade=<child 等級>)`。
- `refresh`（角色無關）：**已因驗 `is_active` 而載入 child**（見 jwt-role §5.4）→ 順手讀**最新**等級重簽 `grade`。故每次 rotation 自動刷新 grade，陳舊窗口 ≤ 一個 access TTL（見決策 R5）。
- **變更等級**：
  - `UserService.set_tier(user_id, tier)` / `AdminService.set_role(admin_id, admin_role)`：寫 child 欄、commit。（升降權的**業務入口**由後續 admin 管理 API 規格提供；本規格只提供 service 能力與授權機制。）

### 5.2 DTO / schema

- `UserResponse` 加 `tier: UserTier`；新增 `AdminResponse`（`id` / `email` / `name` / `is_active` / `admin_role`）。
- **`/me` 是前端等級的真實來源**（每次讀 child 現值、永遠新鮮）；JWT `grade` 只當「零往返初始提示」。

### 5.3 授權 dependencies（`app/api/dependencies/auth.py`）

讀 **child 現值**做階梯判定（非盲信 claim）：

```python
def require_min_admin_role(minimum: AdminRole):
    async def _dep(admin: Admin = Depends(get_current_admin)) -> Admin:
        if ADMIN_ROLE_RANK[AdminRole(admin.admin_role)] < ADMIN_ROLE_RANK[minimum]:
            raise ForbiddenError("insufficient admin role")  # 403
        return admin
    return _dep

def require_min_tier(minimum: UserTier):
    async def _dep(user: User = Depends(get_current_user)) -> User:
        if USER_TIER_RANK[UserTier(user.user_tier)] < USER_TIER_RANK[minimum]:
            raise ForbiddenError("tier required")  # 403
        return user
    return _dep
```

- `get_current_admin` 已載入 `Admin`（含 `admin_role`）→ 階梯檢查是**本地欄位讀取、免額外查詢**。
- 不足 → **403**（已認證但等級不足），與 401（未認證）區分（對齊 jwt-role D6）。

### 5.4 Router 用法

```python
# 讀取類：get_current_admin 即可（VIEWER 以上皆可）
@router.get("/admin/articles", dependencies=[Depends(get_current_admin)])
# 寫入類：EDITOR 以上
@router.post("/admin/articles", dependencies=[Depends(require_min_admin_role(AdminRole.EDITOR))])
# 管理 admin：SUPER_ADMIN
@router.post("/admin/admins",   dependencies=[Depends(require_min_admin_role(AdminRole.SUPER_ADMIN))])
```

### 5.5 前端契約

- **前端可透過 JWT `grade` claim 得知 admin 權限**：解 access token 的 payload（base64url，非驗簽）讀 `grade` → 對 admin（`role==1`）即 `admins.admin_role`（`super_admin`/`editor`/`viewer`），依此顯示/隱藏 CMS 選單與按鈕。
  ```ts
  const { role, grade } = jwtDecode(accessToken);   // role=1, grade="editor"
  if (role === 1) renderCmsByAdminRole(grade);        // grade 即 admin_role
  ```
- **但 UI 的權威來源是 `/me`**：`grade` 只適合「首屏零往返的初始提示」，可能短暫過時；`/me` response 帶 `admin_role`（每次讀 DB、新鮮），前端應以它校正並作為最終渲染依據。
- **`grade` 非授權邊界**：前端竄改解出的值無效——授權由後端 `require_min_admin_role` 讀 child 現值判定（見 §7、決策 R5）。
- **等級變更即時生效**（如降權/付費升級）：後端變更後，前端**強制 refresh 一次** token（或重打 `/me`）。

### 5.6 Seed script（`scripts/create_admin.py` 補強）

初始 admin 佈為 `admin_role = SUPER_ADMIN`（冪等：已存在則確保其為 SUPER_ADMIN 或略過）。

---

## 6. 流程圖

```
簽發：
  login / admin_login ─► 讀 child.等級 ─► access{sub, role, grade=<等級>} + refresh
  refresh             ─► 載 child 驗 is_active（順手讀「最新」等級）─► 重簽 grade

授權（讀 child 現值，階梯）：
  POST /admin/articles + Bearer(role=1) ─► get_current_admin ─► require_min(EDITOR)
      admin_role=viewer  ─► 403
      admin_role≥editor ─► 放行

前端 UI：
  /me ─► { ..., admin_role | tier }（新鮮）─► 渲染畫面
```

---

## 7. 安全性考量

- **claim 不是授權邊界**：前端讀 `grade` 只為 UX；後端一律以 dependency 讀 **child 現值**判定。竄改 claim 無用（後端不信任它做授權）。
- **fail-safe 預設最低權限**：`user_tier` 預設 `FREE`、`admin_role` 預設 `VIEWER`；新建 admin 唯讀，需明確升權（seed admin 例外為 SUPER_ADMIN）。
- **陳舊窗口（等級可變）**：`grade` claim 反映**簽發當下**的等級；升降權後舊 access token 的 claim 會過時 ≤ TTL。緩解：(a) 前端 UI 以 `/me` 為準；(b) refresh 每次重讀 child → 自動刷新；(c) 需即時者強制 refresh。**後端授權讀 child 現值 → 授權判定永遠新鮮**（不受 claim 陳舊影響）。
- **值域硬化**：`CHECK` 擋非法等級字串（integrity-first，對齊 jwt-role D9）。
- **降權即時性**：因授權讀 child，撤/降權對**後端存取控制**是即時的；僅前端 UI 提示可能短暫落後（見上）。

---

## 8. TDD 測試計畫（先寫、先看到 RED）

### 8.1 Unit — enum / JWT grade
- `create_access_token(sub, Role.ADMIN, grade="editor")` → payload `grade == "editor"`；不傳 grade → 無 `grade` key。
- `extract_grade`：缺 → `None`；有 → 對應字串。

### 8.2 Unit — model / DB
- `admin_role` 預設 `VIEWER`、`user_tier` 預設 `FREE`。
- 非法值（`admin_role="root"` / `user_tier="gold"`）→ **`IntegrityError`**（CHECK 生效）。

### 8.3 Unit — service
- `login` / `admin_login` 簽出的 token `grade` == child 當前等級。
- `refresh`：改 `admin.admin_role` 後 refresh → 新 access 的 `grade` 反映**新**值（驗證 rotation 刷新）。
- `set_tier` / `set_role` 寫入 child、回應反映新值。

### 8.4 Unit/Integration — 授權 dependency
- `require_min_admin_role(EDITOR)`：`viewer` → 403；`editor`/`super_admin` → 放行。
- `require_min_admin_role(SUPER_ADMIN)`：`editor` → 403；`super_admin` → 放行。
- `require_min_tier(PREMIUM)`：`free` → 403；`premium` → 放行。
- **授權讀 child 現值**：手簽一個 `grade="super_admin"` 但 DB `admin_role="viewer"` 的 token → 打 SUPER_ADMIN 端點仍 **403**（證明後端不信任 claim、以 DB 為準）。

### 8.5 Integration
- `/me`（user）含 `tier`；admin `/me` 含 `admin_role`，皆反映 DB 現值。
- seed admin 為 `super_admin`，可打管理端點；`viewer` admin 打寫入端點 → 403。

---

## 9. 實作順序（TDD 里程碑）

1. `UserTier` / `AdminRole` enum + rank（8.1）。
2. JWT `grade`（`create_access_token` 加參、`extract_grade`，向後相容）（8.1）。
3. `users.user_tier` / `admins.admin_role` 欄 + CHECK + migration（seed admin 升 SUPER_ADMIN）（8.2）。
4. service 簽發帶 `grade` + `refresh` 刷新 + `set_tier`/`set_role`（8.3）。
5. `require_min_admin_role` / `require_min_tier`（讀 child 現值）（8.4）。
6. `AdminResponse` + `UserResponse.tier` + `/me` 曝露（8.5）。
7. seed script 佈 SUPER_ADMIN（8.5）。
8. 提交前檢查：`ruff` / `ruff format` / `pyright` / `pytest` 全綠；真 MariaDB 驗 upgrade/downgrade。

---

## 10. 已定案決策

- ✅ 方案 A（等級 enum），非方案 B（permissions 表）；權限層與型別判別子分離（見 R1/R2）。
- ✅ `AdminRole = SUPER_ADMIN / EDITOR / VIEWER`（有序階梯）；`UserTier = FREE / PREMIUM`（示範，待確認）。
- ✅ JWT 加 `grade` claim（`StrEnum` 字串，避開 `role` 名）；後端授權讀 child 現值、claim 僅 UX。
- ✅ fail-safe 預設最低權限（`VIEWER` / `FREE`），seed admin 例外 `SUPER_ADMIN`。
- ✅ DB `CHECK` 值域硬化。

## 11. 待確認事項（Open Questions）

1. **`UserTier` 的實際等級**：`FREE / PREMIUM` 為示範；真實分級（是否有 trial / enterprise…）需商業面確認後定案。
2. `user_tier` 是否為「有序階梯」？若不是嚴格階梯（例如不同方案有互不包含的功能），user 側應直接走方案 B（feature/permission 集合），admin 側維持階梯。
3. 升降權的**管理 API**（誰能改誰的等級、稽核）——另立 admin 管理規格；本規格只提供 service 能力與授權機制。
4. 是否要把 `grade` 也納入 `/admin/auth/login` 以外的稽核日誌（admin 等級變更事件）。
