# 規格書：型別內權限等級（方案 A — 等級 enum）

> 🔄 **變更註記（初始 admin 整併）**：seed 腳本已**移除**;初始 super admin 改為 SSM-backed「初始 admin」（`INITIAL_ADMIN_USERNAME` + `INITIAL_ADMIN_PASSWORD_HASH`，不進 DB、恆 `super_admin`、只發 access token；`app/services/initial_admin.py`）。故 §5.6「seed 佈 SUPER_ADMIN」不再適用——初始 super admin 直接由 SSM 憑證登入(合成 super_admin,不寫 `admins` 表)。`AdminRole` enum／`admin_role` 欄／授權面不受影響。

> 狀態：Draft ／ 目標版本：next+1 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 關鍵設計決策與取捨（為什麼這樣設計）另記於 [`../decisions/rbac.md`](../decisions/rbac.md)。本文聚焦「怎麼做」。
>
> 🔗 依賴 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md)（principals supertype、`role` 型別判別子、`grade` claim 掛靠其 JWT 機制）。本規格接手該規格 §11 Open Q3「未來 RBAC 另立規格」。
>
> ⚠️ **與 [`admin-account-refinement.md`](./admin-account-refinement.md)（next）的分工**：`AdminRole` enum、`admins.admin_role` 欄（`String(20)` + `CHECK` + 預設 `VIEWER`）、`create` 帶等級、**seed admin 佈 `SUPER_ADMIN`** 已由 admin-account-refinement **提前交付**（因該規格本就在重建 admins 表）。**本規格（next+1）不再重複建 admin 側 enum/欄/seed**，改**在其上疊授權面**：`ADMIN_ROLE_RANK`、JWT `grade` claim、`require_min_admin_role`、`set_admin_role`，並負責 **user 側全套**（`UserTier`、`users.user_tier`、`require_min_tier`、`set_tier`）。下文 admin 側凡標「（已由 admin-account-refinement 交付）」者僅為銜接說明、非本規格新建。另注意：admin 已改 **username 登入**、`is_active` 為**計算屬性**、`AdminResponse = id/username/name/admin_role`——本規格所有 admin 引用以此為準。

---

## 1. 背景與目標

`jwt-role-and-admin.md` 引入的 `principals.role`（0=user / 1=admin）是**型別判別子**（不可變、決定走哪張 child 表），**不是權限**。本規格在型別**之內**再加一層「權限等級」：

- **Admin 側**：CMS 管理者分三級 `SUPER_ADMIN / EDITOR / VIEWER`（權限高→低的階梯）。
- **User 側**：一般使用者分級 `FREE / PREMIUM`（定案，低→高有序階梯）。升降等級流程（計費/訂閱）暫不考慮，由商業層另議。

採**方案 A（等級 enum）**——每種身分一個獨立的等級 enum，DB 欄位硬化、以有序階梯做授權；**不引入** permissions/roles 關聯表（那是方案 B，另待需求成熟再議，見決策 R1）。

### 目標

- `UserTier`（user 側，**本規格新建**）／`AdminRole`（admin 側，**已由 admin-account-refinement 交付**）兩個 **`StrEnum`**；admin 側的權限**排序**（`ADMIN_ROLE_RANK`）由本規格加。
- `users.user_tier`（**本規格新建**）；`admins.admin_role`（**已由 admin-account-refinement 交付**，含 CHECK + 預設 `VIEWER`）。兩者皆 DB `CHECK` 值域硬化、預設**最低權限** fail-safe。
- JWT 新增 **`grade` claim**（該型別內等級的字串值），供前端渲染 UI；後端授權仍讀 child 現值。
- 授權 dependency：`require_min_admin_role(...)`（階梯）、`require_min_tier(...)`；`/me` 曝露等級供前端。
- seed 初始 admin 佈為 `SUPER_ADMIN`（**已由 admin-account-refinement 的 seed 交付**）。

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

型別判別子 `Role` 是 `IntEnum`（對外整數）；本層等級是**給前端讀的字串**，用 `StrEnum`（對齊既有 `AppEnv`/`LogLevel`）。

**`AdminRole` 已由 [`admin-account-refinement.md`](./admin-account-refinement.md) 定義於 `app/core/enums.py`**（`SUPER_ADMIN`/`EDITOR`/`VIEWER`）——本規格**不重複定義**，僅新增 **`UserTier`** 與兩個 **rank 表**（有序階梯，供 `require_min_*` 比較）：

```python
from enum import StrEnum

from app.core.enums import AdminRole  # 已存在（admin-account-refinement 交付）

# 【本規格新增】user 側等級 enum
class UserTier(StrEnum):
    FREE = "free"
    PREMIUM = "premium"

# 【本規格新增】權限高→低（值越大權限越高）；供 require_min_* 階梯比較
ADMIN_ROLE_RANK: dict[AdminRole, int] = {
    AdminRole.SUPER_ADMIN: 2,
    AdminRole.EDITOR:      1,
    AdminRole.VIEWER:      0,
}
USER_TIER_RANK: dict[UserTier, int] = {
    UserTier.FREE:    0,
    UserTier.PREMIUM: 1,
}
```

> **命名刻意避開 `USER` / `ADMIN`**：`Role.USER` / `Role.ADMIN` 已是型別判別子；admin 等級用 `SUPER_ADMIN / EDITOR / VIEWER`，避免 `AdminRole.USER` 與 `Role.USER` 在程式與 JWT 中混淆（見決策 R3）。此命名決策已在 admin-account-refinement 落實。

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
- `Admin`：**`admin_role` 欄已由 [`admin-account-refinement.md`](./admin-account-refinement.md) §3.1 交付**（`String(20)`、`default`/`server_default` = `VIEWER`、`CHECK ck_admins_admin_role`）——本規格**不再新增此欄**，直接使用。（原設計如下，僅供對照：）
  ```python
  # 已存在於 app/models/admin.py（admin-account-refinement 交付），此處不重複新建
  admin_role: Mapped[str] = mapped_column(
      String(20), default=AdminRole.VIEWER.value, server_default=AdminRole.VIEWER.value
  )
  # CheckConstraint("admin_role IN ('super_admin','editor','viewer')", name="ck_admins_admin_role")
  ```

> **⚠️ `admins` 有兩個「role」相關欄位，用途完全不同、勿混淆**（admin-account-refinement 已於 model 加註解）：
> - `role`（`SmallInteger`，**常數 1**）＝ jwt-role 的**型別判別子**，不可變、被複合 FK + `CHECK(role=1)` 釘死、決定「這是 admin」。**不進 DTO、不對外**。
> - `admin_role`（`String`，`super_admin`/`editor`/`viewer`）＝ 本規格的**權限等級**，可變、供授權與 `grade` claim。
>
> 兩者是「型別 vs 權限」兩個正交軸（見決策 R2）。`users` 同理有 `role`(常數 0) 與 `user_tier`，但名稱夠不同、較無混淆。
>
> **存字串值、程式端以 enum 包裝**（比照 jwt-role 規格 `Role` 存 `SmallInteger`＋`CHECK`）：讀 `AdminRole(admin.admin_role)`、寫 `admin.admin_role = AdminRole.EDITOR.value`。等級是 child **本地欄位**，`get_current_admin` / `get_current_user` 讀它**免 join、免 async footgun**（同 `is_active`，見 jwt-role D4b）。
>
> 亦可改用 `Enum(AdminRole, native_enum=False, length=20)`（SQLAlchemy 在非原生方言自動產 VARCHAR + CHECK）；本規格採「顯式 `String` + 手寫 `CheckConstraint`」以貼齊此 codebase 既有風格。

### 3.3 Migration（新 revision，接 admin-account-refinement 之後）

**本規格只需處理 `users.user_tier`**——`admins.admin_role` 欄與其 `CHECK` 已由 admin-account-refinement 就地建入 `admins` 表（見其 §3.2），此處**不再動 admins**。

1. `users` `ADD COLUMN user_tier VARCHAR(20) NOT NULL DEFAULT 'free'` + `CHECK(user_tier IN ('free','premium'))`。既有列由 default 自動填 `FREE`（無需另行回填）。
2. down：對稱 drop `users.user_tier` 與其 CHECK。

> **seed admin 升 `SUPER_ADMIN` 不在此 migration**：改由 admin-account-refinement 的 **seed script 於建立時直接以 `AdminRole.SUPER_ADMIN` 佈建**（`create(..., admin_role=SUPER_ADMIN)`），非 `UPDATE ... WHERE email`（admin 已無 email 欄）。故本規格無需「回填既有 seed admin」步驟。
>
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

### 4.1 JWT `sid` claim（session 識別，WebSocket 驅動）

> 🔗 由 [`websocket.md`](./websocket.md) §2.5／§2.11 驅動：WebSocket 單一 `logout`（斷某一裝置/session 的連線、不誤斷其他裝置）需讓 WS 連線知道自己屬於哪一次登入 session。`sid` 與 `grade` **同為 optional claim、同一擴充模式**（非 `None` 才放 key、向後相容），故一併記於本節。**WS 模組實作前，此 claim 必須先於 JWT 層落地**（見 websocket §8 里程碑 0）。

`create_access_token` 再疊一個 optional `sid`（承 §4 的 `grade` 擴充）：

- `create_access_token(subject, role: Role = Role.USER, grade: str | None = None, sid: str | None = None) -> str`：`sid` 非 `None` → payload 加 `"sid": sid`（`None` 則不放此 key，向後相容，**比照 `grade`**）。
- `extract_sid(payload) -> str | None`：`payload.get("sid")`（缺 → `None`）。

`sid` = 該登入 refresh 的 `family_id`（`str(uuid4())`）——**穩定識別「同一次登入 session」**（跨多次 rotation 不變，見 refresh-token-rotation §2.4）。一個 editor admin 的 payload：
```jsonc
{ "sub": "7", "role": 1, "grade": "editor", "sid": "3f2b…-uuid4", "type": "access", "iat": ..., "exp": ... }
```

> **語意／邊界**：
> - `sid` = `family_id`（uuid4 字串），**非機密**（僅 session 識別），可直接放明文；傾向不另做 opaque 化（family_id 本非機密）。
> - **非授權邊界**：與 `grade` 相同，後端**不以 `sid` 做授權判定**；它只供 WebSocket 單一 logout 的精準斷線比對（見 websocket §2.5）。
> - **無 `sid` 的 token**（初始 admin `sub=0`、或未帶 `sid` 的舊 token）→ 不參與 sid 級操作（只受 principal 級 kick），向後相容。

---

## 5. 介面設計

### 5.1 Service — 簽發時帶入 grade

- `register` / `login` / `admin_login`：發 access token 時讀 child 的等級 → `create_access_token(principal_id, role, grade=<child 等級>)`。**`register` 亦帶 grade**——register 是 auto-login、同樣發 access token，新建 user 等級恆為最低 `free`。如此**所有簽發 access token 的路徑一致帶 grade**（不只登入），前端首屏零往返即可讀等級，語意一致。
- `refresh`（角色無關）：**已因驗 `is_active` 而載入 child**（見 jwt-role §5.4）→ 順手讀**最新**等級重簽 `grade`。故每次 rotation 自動刷新 grade，陳舊窗口 ≤ 一個 access TTL（見決策 R5）。
- **變更等級**：
  - `UserService.set_tier(user_id, tier)` / `AdminService.set_admin_role(admin_id, admin_role)`：寫 child 欄、commit。（升降權的**業務入口**與完整守衛由 [`admin-management-service.md`](./admin-management-service.md) §3.4 交付；本規格只提供 service 能力與授權機制。**命名採 `set_admin_role`**——改的是 `admins.admin_role` 權限等級，非型別判別子 `role`。）

#### 簽發時帶入 `sid`（§4.1，WS 驅動）

**所有簽發 access token 的路徑，把「當次 session 的 `family_id`」一併傳 `sid`**——與 `grade` 同一處呼叫、多帶一個具名參數即可：

- `login` / `admin_login` / `register`：本來就會產一次 `family_id`（`str(uuid4())`）給 refresh（見 refresh-token-rotation §5.5）→ 把**同一個** `family_id` 傳給 `create_access_token(..., grade=<等級>, sid=family_id)`。**先產 `family_id` 再簽 token**（原本部分路徑是先 `create_access_token` 再 `_issue_refresh_token`，需調整順序讓兩者共用同一個 `family_id`）。
- `refresh`（角色無關）：rotation **保持同一 `family_id`**（`rt.family_id`）→ 新 access token 帶 `sid=rt.family_id`。故**同一 session 跨多次 refresh 的 `sid` 不變**（穩定 session 識別，正是 WS 單一 logout 所需）。
- **初始 admin（SSM，`sub=0`）**：`admin_login` 走 access-only、不建 refresh family → **不帶 `sid`**（`create_access_token(..., sid=None)`）；其 WS 不受單一 logout 影響（本就不走 refresh），只受 principal 級 kick（見 websocket §2.5）。

### 5.2 DTO / schema

- **本規格只需為 user 側加 `UserResponse.tier: UserTier`**。**`AdminResponse` 已含 `admin_role`**（由 admin-account-refinement 交付，形狀為 `id/username/name/admin_role`——注意是 **username 非 email、無 is_active**），本規格不再改它。
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

### 5.6 初始 admin（SSM，取代 seed）

**seed 腳本已移除。** 初始 super admin 改為 **SSM-backed**（`INITIAL_ADMIN_USERNAME` + `INITIAL_ADMIN_PASSWORD_HASH`，argon2 雜湊；不進 DB、哨兵 `principal_id=0`；`app/services/initial_admin.py`）。故原「seed 佈 `SUPER_ADMIN`」不再適用——初始 admin 於登入時**合成** `super_admin`（access token 的 `grade=super_admin`），不寫 `admins` 表。本規格的 `grade`／授權機制不受影響（`require_min_admin_role` 讀合成 Admin 的 `admin_role` 現值，判為 super_admin）。

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

### 8.1 Unit — enum / JWT grade / JWT sid
- `create_access_token(sub, Role.ADMIN, grade="editor")` → payload `grade == "editor"`；不傳 grade → 無 `grade` key。
- `extract_grade`：缺 → `None`；有 → 對應字串。
- **`sid`（§4.1）**：`create_access_token(sub, sid="fam-1")` → payload `sid == "fam-1"`；**不傳 `sid` → 無 `sid` key**（向後相容，比照 grade）。
- `extract_sid`：缺 → `None`；有 → 對應字串。
- `grade` 與 `sid` 可**同時**帶入、互不干擾（`create_access_token(sub, grade="editor", sid="fam-1")` → 兩 key 皆在）。

### 8.2 Unit — model / DB
- `admin_role` 預設 `VIEWER`、`user_tier` 預設 `FREE`。
- 非法值（`admin_role="root"` / `user_tier="gold"`）→ **`IntegrityError`**（CHECK 生效）。

### 8.3 Unit — service
- `login` / `admin_login` 簽出的 token `grade` == child 當前等級。
- `refresh`：改 `admin.admin_role` 後 refresh → 新 access 的 `grade` 反映**新**值（驗證 rotation 刷新）。
- `set_tier` / `set_admin_role` 寫入 child、回應反映新值。
- **`sid`（§5.1）**：`login` / `admin_login` / `register` 簽出的 access token，其 `sid` == 同次簽發之 refresh token 的 `family_id`（解 access token 讀 `sid`，比對 refresh row 的 `family_id`）。
- **`sid` 跨 rotation 穩定**：`refresh` 後新 access token 的 `sid` == 原 `rt.family_id`（同一 session 多次輪替 `sid` 不變）。
- **初始 admin（`sub=0`）** `admin_login` 簽出的 token **無 `sid` key**（access-only、無 refresh family）。

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

> 前置：admin 側 `AdminRole` enum、`admins.admin_role` 欄（+CHECK+預設 VIEWER）、seed SUPER_ADMIN **已由 admin-account-refinement 完成**；以下為本規格增量。

1. `UserTier` enum + `ADMIN_ROLE_RANK` / `USER_TIER_RANK` rank 表（8.1）。
2. JWT `grade`（`create_access_token` 加參、`extract_grade`，向後相容）（8.1）。
   - 同步落地 **`sid`（§4.1）**：`create_access_token` 加 `sid` 參、`extract_sid`；`login`/`admin_login`/`register` 帶入當次 `family_id`、`refresh` 保 `rt.family_id`（§5.1）。**WebSocket 模組的前置里程碑 0**（見 websocket §8），故與 `grade` 同批完成。
3. `users.user_tier` 欄 + CHECK + migration（既有列 default `FREE`）（8.2）。
4. service 簽發帶 `grade` + `refresh` 刷新 + `set_tier`/`set_admin_role`（8.3）。
5. `require_min_admin_role` / `require_min_tier`（讀 child 現值）（8.4）。
6. `UserResponse.tier` + `/me` 曝露（`AdminResponse` 已含 `admin_role`）（8.5）。
7. 提交前檢查：`ruff` / `ruff format` / `pyright` / `pytest` 全綠；真 MariaDB 驗 upgrade/downgrade。

---

## 10. 已定案決策

- ✅ 方案 A（等級 enum），非方案 B（permissions 表）；權限層與型別判別子分離（見 R1/R2）。
- ✅ `AdminRole = SUPER_ADMIN / EDITOR / VIEWER`（有序階梯）；`UserTier = FREE / PREMIUM`（定案，有序階梯；升降流程另議）。
- ✅ **admin 側 enum/欄/預設/seed 由 admin-account-refinement（next）交付；本規格（next+1）疊授權面（rank/grade/require_min）＋ user 側全套**。
- ✅ JWT 加 `grade` claim（`StrEnum` 字串，避開 `role` 名）；後端授權讀 child 現值、claim 僅 UX。
- ✅ JWT 加 **`sid` claim（§4.1，= refresh `family_id`）**：供 WebSocket 單一 logout 精準斷線（websocket §2.5）。optional、非機密、非授權邊界，與 `grade` 同一擴充模式；`login`/`admin_login`/`register`/`refresh` 帶入當次 session 的 `family_id`。**WS 模組前置依賴**（websocket §8 里程碑 0）。
- ✅ fail-safe 預設最低權限（`VIEWER` / `FREE`），seed admin 例外 `SUPER_ADMIN`。
- ✅ DB `CHECK` 值域硬化。

## 11. 待確認事項（Open Questions）

1. ~~**`UserTier` 的實際等級**~~ **已定案**：`FREE / PREMIUM` 為有序階梯，升降流程（計費/訂閱）暫不考慮，由商業層另議。
2. ~~`user_tier` 是否為「有序階梯」？~~ **已定案**：維持有序階梯（`FREE < PREMIUM`）；若未來需要互不包含的功能集，再升方案 B（permission 集合）。
3. 升降權的**管理 API**（誰能改誰的等級、稽核）——**暫不考慮**；本規格只提供 service 能力（`set_tier`）與授權機制（`require_min_tier`）。
4. 是否要把 `grade` 也納入 `/admin/auth/login` 以外的稽核日誌（admin 等級變更事件）——**暫不考慮**。
