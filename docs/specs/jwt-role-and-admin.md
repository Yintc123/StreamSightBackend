# 規格書：JWT Role 機制與 Admin（CMS）角色

> 狀態：**已實作**（enum/JWT role、principals supertype、複合 FK、Admin 模組、授權 dependency、seed 均已落地；migration 手寫待真 MariaDB 驗證，見 §12 cutover）／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 關鍵設計決策與取捨（為什麼這樣設計）另記於 [`../decisions/jwt-role-and-admin.md`](../decisions/jwt-role-and-admin.md)。本文聚焦「怎麼做」（資料模型／介面／流程／測試計畫）。
>
> 🔗 依賴既有的 refresh token 模組 [`refresh-token-rotation.md`](./refresh-token-rotation.md)——本規格會把 refresh token 的擁有者接到新的 **`principals` supertype** 表。
>
> ⚠️ **部分內容已被 [`admin-account-refinement.md`](./admin-account-refinement.md)（next）取代**：本規格描述的 `Admin` 為**當時落地版本**（email 登入、`is_active` 布林欄、物理刪除、`AdminResponse` 含 email）。後續 admin-account-refinement 將其改為 **username 登入、移除 email、`is_active` 為計算屬性（封存 `archived_at` / 軟刪除 `deleted_at`）、新增 `admin_role` 等級欄、`AdminResponse = id/username/name/admin_role`**。**凡涉及 admin 的 email / is_active / 物理刪除 / AdminResponse / seed 之敘述，以 admin-account-refinement 為準**；本規格保留為歷史記錄，principals/複合 FK/JWT role/refresh 機制仍有效不變。

## 1. 背景與目標

目前所有 access token 都代表同一種身分（一般 App 使用者，`User`）。CMS 後台需要一種**獨立於一般使用者**的管理者身分（Admin），且 API 需能從 token 分辨呼叫者角色以做授權。

本模組引入 **role 機制 + principal supertype**：

- 新增 **`principals` 父表**（supertype），承載「帳號共通身分」與 `role` 欄位；`User`、`Admin` 各以 `principal_id` 一對一連上它。
- access token 的 `sub` 改用**全域 `principal_id`**，並帶 `role` claim（整數）。
- 既有一般使用者（`User`）為 **role = 0**；**沒有 `role` claim 的舊 token 一律視為 0**（向後相容）。
- 新增 **`Admin` model**（獨立 `admins` 表，CMS 使用），角色 **role = 1**，以 email + 密碼（argon2）認證。
- refresh token 擁有者接到 `principals`（單一 FK + `ON DELETE CASCADE`），users / admins 共用同一套 rotation / reuse detection，**且保有 DB 層完整性與連帶刪除**。
- 提供**角色授權 dependency**（`get_current_admin` / `require_role` 等），讓 CMS 端點只允許 role = 1。

### 目標

- `Role` enum：`USER = 0`、`ADMIN = 1`（存於 `principals.role`）。
- `principals` supertype 表；`users` / `admins` 各加 `principal_id`（unique FK → principals，CASCADE）。
- `create_access_token(subject, role)` 帶入 `role` claim；`sub` = principal_id；decode 端缺 claim 時預設 0。
- 新增 `admins` 表 + `AdminService` + `AdminRepository`。
- 新增 `POST /admin/auth/login`（admin 以 email/密碼登入，取得 role = 1 的 access + refresh token）。
- 授權 dependency：`get_current_user`（限 role 0）、`get_current_admin`（限 role 1）、`require_role(...)`、`get_current_principal`（logout-all 用）。
- refresh token：`refresh_tokens.user_id` → `principal_id`（FK → principals，CASCADE）；`/auth/refresh`、`/auth/logout` 對兩種角色皆可用。
- 初始 admin 由 **seed script** 佈建（不開放公開註冊）。

### 非目標（Out of scope）

- 具體 CMS 業務端點與 admin 管理 API（建立/列出/停用 admin）——另立規格。
- admin 的 OAuth / 多身分登入（admins 目前只用密碼）。
- 細粒度權限（permission / scope）RBAC——本規格只做「角色」層級（0/1）。

---

## 2. 設計決策

### 2.1 `principals` supertype：一個父表承載所有帳號身分

引入 `principals(id, role, ...)` 作為 **User 與 Admin 的共同父表**（class-table inheritance / 共用父型）：

- `users`、`admins` 各有一欄 `principal_id`（**unique** FK → `principals.id`，`ON DELETE CASCADE`），一對一對應到一筆 principal。
- `role`（0/1）放在 `principals`，是帳號的「型別判別子」（discriminator）。
- **所有「屬於某帳號」的資料（本規格是 `refresh_tokens`，未來可擴充）只要 FK → `principals.id`**，就能拿到跨 user/admin 的統一擁有者，且完整性由 DB 保證。

### 2.2 refresh token 擁有者 = `principals.id`（保有完整性與 CASCADE）

`refresh_tokens.user_id`（FK → users）改為 **`principal_id`（FK → `principals.id`，`ON DELETE CASCADE`）**：

- 一套 rotation / reuse detection / family 連坐 / logout-all 同時服務 users 與 admins。
- `/auth/refresh`、`/auth/logout` 變成**角色無關**（token 對應到一個 principal；refresh 時據 principal 的 role 重簽正確 role 的 access token，天然防提權）。
- **完整性**：每張 token 一定對應一個真實 principal（FK enforce），不可能有孤兒 token；刪除 principal → **CASCADE 自動清掉其 user/admin 列與 refresh token**。相較「無 FK 的 `(role, id)` 多型」，此法不需 app 層記得清理、無安全 footgun。

### 2.3 JWT `sub` 用全域 `principal_id`，`role` claim 快取角色

- `sub` = `principal_id`（**全域唯一**，天然消除 `users.id` / `admins.id` 撞號問題）。
- `role` claim 是 `principals.role` 的快取，讓授權**不必 join principals** 即可分流（見 2.5）。role 由後端於登入時寫入、不可由用戶竄改（JWT 簽章保護）。

### 2.4 兩張獨立 child 表（`users` / `admins`），Admin 自帶 password_hash

- 一般使用者與 CMS 管理者是不同生命週期、不同關注點的實體，故 `User`、`Admin` **各自成表**（都掛在 `principals` 下）。
- **Admin 憑證**：`admins` 直接放 `password_hash`（argon2id），不沿用 `Identity` 多身分——CMS admin 只需密碼登入，多身分是過度設計（見 [`identity-constraints.md`](../decisions/identity-constraints.md)）。未來要 admin OAuth 再另立 `AdminIdentity`。

### 2.5 授權以 dependency 表達（fail-safe，預設拒絕）

- `get_current_user`：要求 `role == 0`，回 `User`；admin token（role 1）→ `ForbiddenError`(403)。
- `get_current_admin`：要求 `role == 1`，回 `Admin`；user token（role 0/缺）→ 403。
- `require_role(*roles)`：factory，回傳檢查角色的 dependency。
- `get_current_principal`：**不查 DB**，直接由驗過簽的 token 取 `(principal_id, role)` 回一個輕量值物件（見 §5.6），供 `logout-all` 等「角色無關但只需當前身分 id/role」的端點。
- 角色不符一律 **403 Forbidden**（已認證但越權），與「未認證」401 區分；缺 role → fail-safe 視為最低權限 role 0。

### 2.6 建立/刪除的交易語意

- **建立**：先建 `principals(role=...)` 取得 id → 再建 child（`user`/`admin`，帶 `principal_id`），**同一交易**完成（避免半殘 principal）。
- **刪除**：刪除**該 principal 列**即可，`ON DELETE CASCADE` 連帶刪掉 child（→ 再連帶 `identities`）與 `refresh_tokens`。service 的 `delete` 以 principal 為單位刪除（見 §5）。

### 2.7 Admin 佈建：seed script，不公開註冊

CMS admin 屬高權限，不提供 `/admin/register`；初始 admin 以一次性 **seed script**（讀環境變數初始帳密、冪等）建立；後續 admin 管理 API 另立規格。

---

## 3. 資料模型

### 3.1 `Role` enum（`app/core/enums.py`）

其他 enum 是 `StrEnum`；role 對外是整數 claim，用 `IntEnum`：

```python
from enum import IntEnum

class Role(IntEnum):
    USER = 0
    ADMIN = 1
```

### 3.2 `Principal` supertype：`app/models/principal.py`

```python
class Principal(Base):
    __tablename__ = "principals"
    # role 判別子（0=User, 1=Admin），帳號建立後不可變
    # 不加獨立 index：role 只有兩個值（低選擇性），且沒有任何熱路徑會「用 role 查 principals」
    # （refresh 走 principals.get(id) 是 PK lookup；要列 admin 直接查 admins 表）。
    # 下方 UNIQUE(id, role) 已足以當複合 FK 的參照目標，故不需 index=True。
    role: Mapped[int] = mapped_column(SmallInteger)
    __table_args__ = (
        # 供 child 的複合 FK 參照（型別-角色一致性硬化，見 §3.3）；id 已是 PK，此約束純為 FK 目標
        UniqueConstraint("id", "role", name="uq_principals_id_role"),
        # 父表 role 值域硬化：DB 層擋掉無對應 child 型別的 role（如 role=5）。
        # child 已用 CHECK(role=0/1) 釘死，父表也一致鎖定值域，對齊 integrity-first（見決策 D9）。
        CheckConstraint("role IN (0, 1)", name="ck_principals_role_domain"),
    )
```

`id` / `created_at` / `updated_at` 由 `Base` 提供。父表**只放判別子 `role`**。

> **邊界原則（哪些欄位放父表）**：只上移「子型別**共有**、且**角色無關存取路徑**（如 refresh 無 role claim、非查父表不可）剛性需要」的欄位。`role` 是型別判別子、建立後不可變、且 refresh 等路徑必須從父表取得 → 放父表。**`is_active`、`email`、`name` 一律留在 child**：
> - `is_active` 雖為共有狀態，但幾乎所有讀它的路徑（`get_current_user`/`get_current_admin`/list users/`UserResponse`）都是**已知型別的高頻 child 存取**——放 child 讓這些路徑讀「本地欄位」即可，免 join，且在 async 下**結構上免疫** `MissingGreenlet`（見決策 [`../decisions/jwt-role-and-admin.md`](../decisions/jwt-role-and-admin.md) D4b）。
> - `email` 若上移並全域 unique，等於禁止「同一 email 同時是 user 又是 admin」，破壞各自獨立的命名空間。
>
> 故父表只承載判別子 `role`；帳號狀態與識別屬性都留 child。

### 3.3 `User` / `Admin` 掛上 principal

- `User`（改）：**新增 `principal_id` + 常數判別欄 `role=0` + 複合 FK；`is_active` 保留在 `users` 不動**：
  ```python
  principal_id: Mapped[int] = mapped_column(unique=True, index=True)  # 複合 FK 承擔參照，不再 inline ForeignKey
  role: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))  # 常數，永遠 0
  # 保留不變：is_active、加密 email、name、Identity 關係
  # principal 關聯可選（非熱路徑用；不需 lazy="joined"、不用於讀 is_active）
  __table_args__ = (
      ForeignKeyConstraint(
          ["principal_id", "role"], ["principals.id", "principals.role"],
          ondelete="CASCADE", name="fk_users_principal_role",
      ),
      CheckConstraint("role = 0", name="ck_users_role_user"),  # 釘死型別，不能被改成 1
  )
  ```
  `email` / `name` / `is_active` 全留在 `users`（見 §3.2 邊界原則）。既有 `user.is_active` / `UserResponse` **零改動**（`role` 常數欄不進 DTO）。〔範圍註：此「零改動」僅限本規格階段；後續 [`rbac.md`](./rbac.md) 會再為 `UserResponse` 加 `tier` 欄。〕
- `Admin`（新，`app/models/admin.py`）〔⚠️ 此 email + `is_active` 布林版本**已被 [`admin-account-refinement.md`](./admin-account-refinement.md) 取代**：改 username、`is_active` 為計算屬性、加 `admin_role`；見文首取代註記〕：**自帶 `is_active` 欄位**，同樣加常數判別欄 `role=1` + 複合 FK：
  ```python
  class Admin(Base):
      __tablename__ = "admins"
      principal_id: Mapped[int] = mapped_column(unique=True, index=True)
      role: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))  # 常數，永遠 1
      email: Mapped[str] = mapped_column(
          DeterministicEncryptedString(key=_ENCRYPTION_KEY, length=1024), unique=True, index=True
      )
      name: Mapped[str] = mapped_column(String(100))
      password_hash: Mapped[str] = mapped_column(String(255))  # argon2id
      is_active: Mapped[bool] = mapped_column(default=True)
      __table_args__ = (
          ForeignKeyConstraint(
              ["principal_id", "role"], ["principals.id", "principals.role"],
              ondelete="CASCADE", name="fk_admins_principal_role",
          ),
          CheckConstraint("role = 1", name="ck_admins_role_admin"),
      )
  ```
  `_ENCRYPTION_KEY` 比照 `app/models/user.py` module 綁定。

> **無 async eager-load footgun**：`is_active` 是 `users` / `admins` 的**本地欄位**，讀取即讀已載入的那一列，**不觸發任何關聯 lazy load**，async 下天生免疫 `MissingGreenlet`，也無需 `lazy="joined"` 或 `association_proxy`。`get_current_user` / `get_current_admin` / `list_all` / `UserResponse` 讀 `is_active` 都是單表本地讀取。**變更帳號啟停**時對 `user.is_active` / `admin.is_active` 寫入（各自 child，見 §5）。

> **Alembic 偵測前提**：於 `app/models/__init__.py` 匯入並列入 `__all__`：`Principal`、`Admin`（`User` 已在）。

> **子型別-角色一致性（複合 FK 硬化，既定）**：DB 單純 FK 擋不住「user 連到 role=1 的 principal」，故**既定**以複合 FK 硬化——`principals` 加 `UNIQUE(id, role)`，`users`/`admins` 各帶常數欄 `role`（0/1）並以 `FK (principal_id, role) → principals(id, role)` 綁定，再加 `CHECK(role=…)` 釘死常數。如此「型別-角色錯配」在 DB 層即 IntegrityError，不倚賴 service 記得配對（對齊本專案 integrity-first 風格，見決策 D9）。service 建立時仍應配對正確，複合 FK 為 defense-in-depth 的最後一道。
>
> 註：`CheckConstraint` 於 MariaDB 10.2+ 與 SQLite 皆實際強制；`ForeignKeyConstraint` / `CheckConstraint` / `UniqueConstraint` 需自 `sqlalchemy` import，`text` 亦然。複合 FK 已帶 `ondelete="CASCADE"`，故 child 的刪除連帶行為不變（§7 方向 A 仍成立）。

### 3.4 `RefreshToken`（改）：擁有者接到 principals

`app/models/refresh_token.py`：把 `user_id`（FK → users）換成 `principal_id`（FK → principals）：

```python
# 移除：user_id ... ForeignKey("users.id", ondelete="CASCADE")
principal_id: Mapped[int] = mapped_column(
    ForeignKey("principals.id", ondelete="CASCADE"), index=True
)
# token_hash / family_id / expires_at / revoked_at / replaced_by_id 不變
```

### 3.5 Migration（新 revision，接 `9f3c1a4b2d7e`）

一支（或按需拆數支）migration：

1. **建 `principals`**（`id` PK、`role` SMALLINT NOT NULL（**不加獨立 index**，見 §3.2）、時間戳 server_default；**無 `is_active` 欄**）。加 `UNIQUE(id, role)` 約束（`uq_principals_id_role`，供 child 複合 FK 參照）＋ `CHECK(role IN (0,1))`（`ck_principals_role_domain`，父表 role 值域硬化）。
2. **`users` 掛 principal + 常數 role 欄 + 複合 FK**（`is_active` 留在 `users` 不動）：
   - `ADD COLUMN principal_id INT NULL`、`ADD COLUMN role SMALLINT NOT NULL DEFAULT 0`。
   - **回填（保留 id：`principal.id` 直接沿用 `user.id`）**：以**顯式 id** 建 principal，讓每個既有 user 的 `principal_id` 等於自己的 `user.id`——這是消除 §7「sub 碰撞」風險、且不需輪替 secret 的關鍵前提（見下方 ✅ 與 §7）。此法比「新密集序列 + 視窗函數對齊」**更簡單也更安全**：
     ```sql
     -- 保留 id：顯式帶入 id，令 principal.id == user.id（非新序列，天然 1:1）
     INSERT INTO principals (id, role, created_at, updated_at)
       SELECT id, 0, created_at, updated_at FROM users;
     -- principal_id 直接等於自己的 id（無需視窗函數、無錯位可能）
     UPDATE users SET principal_id = id;
     -- 把 principals 的 AUTO_INCREMENT 推到 max(user.id)+1，新 admin/user 從空洞之上取號
     -- MariaDB：ALTER TABLE principals AUTO_INCREMENT = <max(users.id)+1>;（migration 內以 inspector 取 max 再設）
     ```
     **`users.is_active` 不動、不搬、不 drop。**
   - `principal_id` 改 `NOT NULL` + `UNIQUE` + index。
   - **回填後驗證（migration 內或緊接的一次性檢查）**：斷言「每個 user 都有唯一 principal、且 `principal_id == id`」——
     ```sql
     -- 應為 0：沒有對到 principal 的 user
     SELECT COUNT(*) FROM users WHERE principal_id IS NULL;
     -- 應為 0：principal_id 與 id 不一致者（保留 id 後必為 0）
     SELECT COUNT(*) FROM users WHERE principal_id <> id;
     ```
     ✅ **保留 id 的關鍵好處**：因既有 user 的 `principal_id == user.id`，舊 access token 的 `sub`（= 舊 user.id）在新系統仍解析到**同一個** user（`get_by_principal_id(舊 user.id)` → 正確 user）。**§7 的帳號混淆風險因此不存在，cutover 不需輪替 `JWT_SECRET_KEY`、使用者無需重新登入。** admin 的 principal id 落在 `max(user.id)` 之上、admins 自身 PK 獨立，無撞號。（新註冊的 user 之 `principal_id` 由序列取號、與其 `user.id` 不必相等——但那類帳號在 cutover 時尚無舊 token，不受影響。）
   - 加**複合 FK** `(principal_id, role) → principals(id, role)` `ON DELETE CASCADE`（`fk_users_principal_role`）+ `CHECK(role = 0)`（`ck_users_role_user`）。
3. **建 `admins`**（欄位如 §3.3；含 `principal_id` NOT NULL UNIQUE、常數 `role SMALLINT NOT NULL DEFAULT 1`、`email` unique index、`password_hash`、`is_active` BOOL NOT NULL default 1；複合 FK `fk_admins_principal_role` + `CHECK(role = 1)` `ck_admins_role_admin`）。admins 無既有資料，不需回填。
4. **`refresh_tokens` 換擁有者**：
   - `ADD COLUMN principal_id INT NULL` → 回填 `UPDATE refresh_tokens rt JOIN users u ON rt.user_id=u.id SET rt.principal_id=u.principal_id`。
   - **DROP FK**（MariaDB 自動命名如 `refresh_tokens_ibfk_1`；以 inspector 取得實際名再 drop，勿寫死）→ drop `ix_refresh_tokens_user_id` → `DROP COLUMN user_id`。
   - `principal_id` 改 `NOT NULL` + FK(CASCADE) + index `ix_refresh_tokens_principal_id`。

> down 反向對稱還原（僅需支援 role 0 的 user 還原；一併移除 users 的 `role` 欄、複合 FK、CHECK 與 principals 的 `uq_principals_id_role`、`ck_principals_role_domain`）。⚠️ 產出後**人工檢視**，並在真 MariaDB 跑 `upgrade head` / `downgrade` 驗證（測試走 SQLite `create_all`，不經 migration；**測試需開 `PRAGMA foreign_keys=ON` 才會強制複合 FK**——conftest 已開）。

---

## 4. 設定（Config）

- `.env.example` 補初始 admin seed 用變數（僅 seed script 讀取，app runtime 非必要）：

```bash
# 初始 CMS admin（供 seed script 建立；建立後可移除）
INITIAL_ADMIN_EMAIL=admin@example.com
INITIAL_ADMIN_PASSWORD=change-me-strong-password
```

- `BaseAppSettings` 新增 `initial_admin_email: str = ""` / `initial_admin_password: SecretStr = SecretStr("")`（空值時 seed script 報錯提示；app 啟動不依賴）。

---

## 5. 介面設計

### 5.1 JWT `app/core/auth/jwt.py`

- `create_access_token(subject: str | int, role: Role = Role.USER) -> str`：`subject` 傳 **principal_id**；payload 新增 `"role": int(role)`。預設 `Role.USER` 讓既有不傳 role 的呼叫端自動得 0（不破壞簽名）。
- `extract_role(payload) -> Role`：**fail-safe 解析**——缺 claim **或**未知值都退回最低權限 `Role.USER`：
  ```python
  def extract_role(payload: dict) -> Role:
      try:
          return Role(payload.get("role", Role.USER))
      except ValueError:
          # 未來版本簽出的未知 role（如 role=2）被舊 server 解到 → 降權，不 500
          return Role.USER
  ```
  > 只寫 `Role(payload.get("role", Role.USER))` 只處理「缺 claim」；若 claim 是**未知整數**，`Role(2)` 會 `ValueError` → 500。fail-safe 降到最低權限與 D6/D8 一致（見決策 D8）。

### 5.2 Repository

- `PrincipalRepository(BaseRepository[Principal])`：`create(role) -> Principal`（供 register / admin 建立）；`get(id) -> Principal | None`（refresh 取 role 用）。於 `repositories/__init__` 匯出。
- `UserRepository`（改／補）：新增 `get_by_principal_id(principal_id) -> User | None`。`get` / `get_by_email` / `list_all` **維持原樣**（`is_active` 是本地欄位，無需 eager-load principal、無 async footgun）。
- `AdminRepository(BaseRepository[Admin])`：`get_by_email(email)`、`get_by_principal_id(principal_id)`。匯出。
- `RefreshTokenRepository`（改）：擁有者由 `user_id` 改 `principal_id`：
  - `revoke_all_for_user(user_id, ...)` → **`revoke_all_for_principal(principal_id, revoked_at) -> int`**。
  - `get_by_hash` / `consume` / `revoke_family` / `delete_expired` 不變。
  - **不再需要** `delete_for_principal`（刪除靠 principals 的 FK CASCADE）。

### 5.3 DTO / schema

- admin 登入直接**複用 `LoginRequest`**（email + password，同結構），語意由端點區分。
- `TokenPayload` / `TokenResponse` 不變（role 不外露於 response body；權限由後端判定）。

### 5.4 Service — 使用者側（改動）

`app/services/auth.py`（`AuthService`），注入 `PrincipalRepository`：
- **`register`**：建 `principal = Principal(role=Role.USER)` → 建 `User(principal_id=principal.id, ...)`（`is_active` 用預設 True）→ 建 `Identity` → `access = create_access_token(principal.id, Role.USER)` → `_issue_refresh_token(principal.id, family)`。
  - **交易原子性（本規格最大的實作風險，需優先處理）**：`principal` 與 `user` 必須在**同一 flush/commit** 內落地（避免 principal 先 commit、user 失敗 → 孤兒 principal）。
    > **根因不只在本規格**：現有 `register` **今天就已經有三個獨立 commit 點**（`UserService.create` 自行 commit、`identity_repo.add` 後 commit、`_issue_refresh_token` 後 commit）——意即**現況的 register 本就不是原子的**，crash 在中間會留下「有 user 無 identity」等半殘狀態。本規格在最前面再插入 `principals.create()` 只會讓問題多一種半殘型態（孤兒 principal）。
    >
    > **正解：落實 Unit-of-Work，而非把三 commit 變四 commit。** repository 一律只 `flush`、**絕不 `commit`**；由 service 的 use-case 方法（`register` / `AdminService.create`）持有**唯一一次**交易，讓 `principal + user + identity + refresh` 在**同一 commit** 落地，失敗則整批 rollback。這同時修掉既有 register 的非原子性。
    >
    > 具體調整：`UserService.create` 拆出一個**不 commit** 的建立路徑（例如 `build_user(...)` 或 `create(..., commit=False)`），`register` 呼叫它並在流程末端統一 commit 一次。此重構列為里程碑 §9 的獨立步驟（見 §9 step 3.5），且需有「register 中途失敗 → 資料庫不留任何 principal/user/identity/token」的測試（見 §8.4）。
- `_issue_refresh_token` 簽名改 `(principal_id, family_id)`。
- **`login`**：verify 後 `create_access_token(user.principal_id, Role.USER)` + `_issue_refresh_token(user.principal_id, family)`。
  > **is_active 一致性**：現有 `/auth/login` **不檢查 `user.is_active`**（被停用的 user 仍能拿到 token，只是後續 `get_current_user` 會擋下）。Admin 側 `admin_login` 規格（§5.5、§8.4）**會**在登入時擋 `is_active=false`。為兩條路徑語意一致、且避免「發出注定被拒的 token」，**順手補上 user login 的 `is_active` 檢查**（停用 → `UnauthorizedError`，沿用統一模糊訊息）。此為既有缺口，趁本規格改動一併修正。
- **`refresh`**：讀出 `rt.principal_id` → **查 `principals.get(rt.principal_id)` 取得 `role`**（refresh 無 role claim，必須查父表定型別＋重簽）→ 依 role 分流載入對應 child（`user_repo` / `admin_repo` 的 `get_by_principal_id`）**讀 `child.is_active`**；principal 不存在、child 不存在或 `is_active` 為 false → 401 → 重簽 `create_access_token(principal_id, principal.role)` + rotate（原子消費、grace、commit-before-raise 等**行為不變**，見 refresh 規格）。
  > 相較今日 refresh 已在讀 `user.is_active`，此處僅多「依 role 挑 child repo」的分支；驗 active 為 principal + child 兩次 indexed lookup（refresh 屬低頻，可忽略）。
- **`logout`**：不變（token-based）。**`logout_all`**：改吃當前 principal → `revoke_all_for_principal(principal_id)`。
- **刪除**：`UserService.delete(user_id)` 改為「解析 `user.principal_id` → 刪 `principals` 該列」，CASCADE 連帶清 user + identities + refresh_tokens（不需再手動清 token）。
- **停用帳號**：對 `user.is_active` / `admin.is_active` 寫入（各自 child；語意一致）。

### 5.5 Service — Admin 側（新增）

- `AdminService`（`app/services/admin.py`）：`get(id)` / `get_by_email` / `get_by_principal_id` / `create(email, name, password)`（供 seed：建 `Principal(role=ADMIN)` → 建 `Admin(principal_id=principal.id, ...)`，**同一交易**，argon2 hash 密碼）/ `delete(admin_id)`（解析 principal_id → 刪 principal，CASCADE）。
- Admin 登入：驗證 `admins` email + 密碼（`verify_password`），發 `create_access_token(admin.principal_id, Role.ADMIN)` + `_issue_refresh_token(admin.principal_id, family)`。實作**共用一處 refresh 發放邏輯**（`AuthService.admin_login` 或注入共用 helper），避免重複。

### 5.6 授權 dependencies `app/api/dependencies/auth.py`

沿用 `OAuth2PasswordBearer`；解碼後 `sub = principal_id`、`role = extract_role(payload)`，據 role 分流：

```python
async def get_current_user(...) -> User:      # role 必須 0，否則 403；user_repo.get_by_principal_id(sub)
async def get_current_admin(...) -> Admin:    # role 必須 1，否則 403；admin_repo.get_by_principal_id(sub)
def require_role(*roles: Role): ...           # factory → dependency
async def get_current_principal(...) -> CurrentPrincipal:  # logout-all 用；不查 DB，由 token 取 (id, role)
```

- **輕量值物件**：`get_current_principal` **不回 ORM `Principal`、不查 DB**，回一個 frozen 值物件（放 `app/dtos/` 或 dependencies 內）：
  ```python
  @dataclass(frozen=True)
  class CurrentPrincipal:
      id: int      # = sub（principal_id）
      role: Role
  ```
  logout-all 只需 `principal_id`（來自已驗簽的 `sub`）＋ `role`（來自 claim），兩者都在 token 裡；`revoke_all_for_principal(principal_id)` 對不存在的 id 也只是無害 no-op，故**無需 DB 往返**。
- service 層提供 `get_user_from_token` / `get_admin_from_token`（皆查 DB 解析 child）＋ `get_principal_from_token`（**只驗簽 + 解析 `(id, role)`，不查 DB**，回 `CurrentPrincipal`）；dependency 薄封裝（比照現行 `get_current_user`）。
- 既有 `get_current_user` **加 role 檢查**：role 非 0 → 403。
- **active 檢查讀 child 本地 `is_active`**（`get_current_user` 讀 `user.is_active`、`get_current_admin` 讀 `admin.is_active`，單表本地讀取、免 join）；非 active → 401（沿用現行「User not found or inactive」語意）。
- **`get_current_principal`（logout-all）不做 is_active 檢查、不查 DB**：logout-all 是「撤銷自己 token」的降權動作，停用帳號亦應允許；只需 token 內的 id/role，不必解析 child、不必載入 principal。

### 5.7 Router

- **新增** `app/api/routers/admin/`（目前空 placeholder）：
  - `POST /admin/auth/login` → admin login → `200` `TokenResponse`（role 1 的 access + refresh + expires_in）。
  - 於 `admin/__init__.py` 匯出 `router`，並在 `api/routers/__init__.py` + `api/__init__.py` 把 `admin_router` 掛進 `api_router`。
- refresh / logout / logout-all **維持在 `/auth/*`**（角色無關）；`logout-all` 改用 `get_current_principal`。

### 5.8 Seed script

- `scripts/create_admin.py`：讀 `initial_admin_email` / `initial_admin_password`，冪等建立（存在則略過）第一個 admin（走 `AdminService.create`）。以 `uv run python -m scripts.create_admin` 執行；不進 API、不進 CI 自動跑。

---

## 6. 流程圖

```
建立（交易內兩步）：
  register ─► principals.create(role=0) ─► users(principal_id, email, ...) ─► identity ─► tokens(sub=principal_id, role=0)
  seed     ─► principals.create(role=1) ─► admins(principal_id, email, password_hash)

登入：
  POST /auth/login        ─► User verify  ─► access{sub=principal_id, role=0} + refresh{principal_id}
  POST /admin/auth/login  ─► Admin verify ─► access{sub=principal_id, role=1} + refresh{principal_id}

授權：
  GET /users/me  + Bearer(role=0) ─► get_current_user  ─► User   (role=1 → 403)
  GET /admin/... + Bearer(role=1) ─► get_current_admin ─► Admin  (role=0/缺 → 403)

Refresh（角色無關；owner=principal）：
  POST /auth/refresh {refresh_token} ─► principals.get(principal_id) [role] ─► 依 role 載 child 讀 is_active ─► 驗 active ─► 重簽同 role access + rotate

刪除（CASCADE）：
  delete principal ─► child(user/admin) + identities + refresh_tokens 全部連帶刪除
```

---

## 7. 安全性考量

- **id 撞號隔離**：`sub` 是全域 `principal_id`，本質上不會撞號；`role` claim 再明確分流授權。
- **完整性由三種機制分工保證**（各管一種，互不重疊，不要混為一談）：

  | 完整性面向 | 何時可能被破壞 | 由誰保證 |
  |---|---|---|
  | **無懸空 child**（parent 被刪、child/token 沒跟著清） | 刪除時 | **FK + `ON DELETE CASCADE`**（含 child 的複合 FK）：刪 principal 連帶清 user/admin + identities + refresh_tokens，**無「忘記清理」footgun**（相較無 FK 多型） |
  | **無孤兒 principal**（principal 沒有對應 child） | 建立半途失敗時 | **交易原子性**：principal 與 child 必須同一 flush/commit 落地（見 §2.6/§5.4）。⚠️ FK 是 child→parent 單向，**擋不到「parent 沒 child」**，故非 FK 能負責 |
  | **無型別-角色錯配**（user 掛到 role=1 的 principal，反之亦然） | 建立配錯時 | **複合 FK（既定）** `(principal_id, role)→principals(id, role)` + `CHECK(role=…)` 於 DB 硬化（見 §3.3、決策 D9）；service 建立時仍配對正確，複合 FK 為 defense-in-depth |

- **fail-safe 授權**：dependency 預設拒絕；缺 role → 最低權限 role 0，絕不預設 admin。
- **角色不符回 403**（已認證但越權），與 401（未認證）區分。
- **refresh 不可提權**：新 access token 的 role 來自 principal（DB），非用戶可控。
- **admin 不可公開註冊**；密碼 argon2id；沿用統一模糊錯誤訊息、`mask_email` log、pepper 雜湊 refresh token。
- **admin 登入暴力破解防護（已知缺口）**：admin 是高價值目標，但本規格目前**僅靠「統一模糊錯誤訊息」**防列舉，**未含速率限制 / 帳號鎖定**。最佳實踐應對 `/admin/auth/login` 加上失敗次數限制（可複用既有 Redis：key 以 `mask_email` 或來源 IP 計數，超閾值暫時鎖定）。**本規格範圍暫不實作**，但明列為已知缺口，建議緊接一支後續規格處理；上線前至少於基礎設施層（反向代理 / WAF）對該端點限流。
- **無狀態 access token 撤銷窗口（已接受的取捨）**：access token 是無狀態 JWT，`logout` / `logout-all` **只撤 refresh token**；已簽發的 access token 會**存活到 `exp`**（全域 30 分，admin 亦同——見 §10 決定維持）。意即帳號停用 / 登出後，最多仍有 ≤30 分的 access 有效窗口。這是 stateless JWT 的固有性質、且與現行 user 行為一致，**本規格有意識保留**；若未來需要對 admin 即時撤銷，選項為（a）縮短 admin TTL（per-role TTL）或（b）為 admin 導入 access-token denylist（Redis），屆時另議。

> **✅ 部署過渡（`sub` 語意變更）——已由「保留 id」化解，不需輪替 secret、不需重新登入**：
> `sub` 由「`user.id`」概念上改為「`principal_id`」，但 migration **保留 id**（`principal.id == user.id`，見 §3.5 step 2）：既有 user 的 `principal_id` 就等於自己的 `user.id`。因此舊 access token 的 `sub`（= 舊 user.id）在新系統仍解析到**同一個** user：
>
> ```
> 舊 token sub="42"（原 user 42，無 role claim → D8 預設 role 0）
>   → get_current_user 載入 user_repo.get_by_principal_id(42)
>   → principal 42 == user 42（保留 id）→ 回傳正確的 user，role 0 正確
> ```
>
> 無帳號混淆、無水平越權。D8 的「無 role claim 視為 0」在此路徑上**正是正確的向後相容**（舊 user token 天然是 role 0）。
>
> **為何不會撞號**：既有 user 佔用 `[1..max(user.id)]` 的 principal id；migration 後把 principals 的 AUTO_INCREMENT 推到 `max(user.id)+1`，新 admin（與新 user）的 principal_id 一律由此之上取號 → 與既有 user 的 id 空間**永不重疊**，admins 自身 PK 亦獨立。
>
> **部署 checklist**：(1) 部署新程式碼與 migration（含保留 id 回填 + 把 principals AUTO_INCREMENT 設到 `max(user.id)+1`）；(2) **無需輪替 `JWT_SECRET_KEY`、無需強制重新登入**；(3) 例行監控即可。既有 access token 與 refresh token 皆不受影響。
>
> > 備援：僅當未來某次遷移**無法保留 id**（如跨庫搬遷重編號）時，才需回到「cutover 輪替 `JWT_SECRET_KEY` 使舊 access token 失效、client 以 refresh token 換發」的舊策略。本規格因保留 id 而不需要。

---

## 8. TDD 測試計畫（先寫、先看到 RED）

> 由內而外（enum/jwt → principals/model/repo → service → dependency → api）。

### 8.1 Unit — JWT role（擴充 `tests/unit/test_jwt.py`）
- `create_access_token(sub, Role.ADMIN)` → `role == 1`；不傳 role → `role == 0`。
- `extract_role`：無 claim → `USER`；有 → 對應值。

### 8.2 Unit — Principal / User / Admin repository（`tests/unit/repositories/`）
- `PrincipalRepository.create(role)` 回帶 id 的列。
- `UserRepository.get_by_principal_id` 取回；`AdminRepository.get_by_email` / `get_by_principal_id`；admin email unique（重複 → IntegrityError）。
- **CASCADE**：刪 `principals` 該列 → 對應 user/admin **與其 refresh_tokens** 一併消失（驗證完整性；此為取代 app 層清理的關鍵測試）。
- **複合 FK（型別-角色一致性）**：把 `User` 掛到 role=1 的 principal（或 `Admin` 掛 role=0）→ **`IntegrityError`**（驗證 DB 硬化擋住錯配；需 `PRAGMA foreign_keys=ON`）。正確配對（user↔role0、admin↔role1）→ 成功。
- **principals.role 值域**：建 `Principal(role=5)` → **`IntegrityError`**（`ck_principals_role_domain` 擋掉無對應 child 型別的 role；SQLite 需 CHECK 生效）。`role=0` / `role=1` → 成功。

### 8.3 Unit — RefreshTokenRepository（改 `test_refresh_token.py`）
- 既有測試由 `user_id` 改 `principal_id`。
- `revoke_all_for_principal` 只撤該 principal 的 active token，不影響其他 principal。
- `get_by_hash` / `consume` / `revoke_family` / `delete_expired` 行為不變。

### 8.4 Unit — Service（`tests/unit/services/`）
- `register`：建立 principal(role=0) + user + identity；回傳 token `sub == user.principal_id`。
- **`register` 交易原子性（Unit-of-Work）**：模擬流程中途失敗（例如 identity 建立時拋例外）→ **資料庫不留任何 principal / user / identity / refresh_token**（斷言四張表對該 email 皆 0 列）。這是 §5.4 UoW 重構的守門測試，防止孤兒 principal 與既有的半殘 register。
- **`login` is_active**：`user.is_active = false` 的帳號打 `/auth/login`（正確帳密）→ `UnauthorizedError`（統一訊息），**不發出 token**；與 admin_login 語意一致。
- `AuthService.refresh`：user refresh 後新 access 仍 role 0；admin refresh 後仍 role 1；rotation/reuse 行為不變。
- `admin_login`：正確帳密 → role 1 token + refresh；錯密碼/停用/不存在 → `UnauthorizedError`（統一訊息）。
- **停用**（user 設 `user.is_active = false`、admin 設 `admin.is_active = false`）→ 該帳號 `refresh`、`get_current_user` / `get_current_admin` 皆拒（401）；user 與 admin 一致。
- `user.is_active` 維持本地欄位（`UserResponse.model_validate(user)` 仍含 `is_active`；既有測試零改動）。
- `update`（含 `is_active`）→ 寫入 `user.is_active`，回應反映新值（同今日行為，不變）。
- `UserService.delete` / `AdminService.delete`：刪除後該帳號的 refresh token 一併消失（經 principal CASCADE）。
- 既有 refresh service 測試改用 principal 介面，行為不變。

### 8.5 Unit/Integration — 授權 dependency
- `get_current_user`：role 0 → User；role 1 → 403。
- `get_current_admin`：role 1 → Admin；role 0/缺 → 403。
- `require_role`：符合放行、不符 403。
- **向後相容（fail-safe）**：無 `role` claim 但以現行 `JWT_SECRET_KEY` 簽出的 token → `get_current_user` 視為 role 0 放行（以手簽無 role 的 token 建構）。此為對「缺 claim」的 fail-safe 降權。
- **過渡期安全（保留 id）**：構造 `principal_id == id` 的資料佈局（模擬 migration 保留 id）後，手簽一個**無 role claim、`sub` = 該 user 的 id** 的 token → `get_current_user` 必須解析到**同一個正確 user**（放行，且回傳的正是 principal N 本人，不可能是別人）。此測試釘死 §7「保留 id 讓舊 token 續用、不需輪替 secret、且不發生帳號混淆」的過渡策略。

### 8.6 Integration（`tests/integration/test_admin_auth_api.py`）
- `POST /admin/auth/login` 正確 → `200`（含 refresh + expires_in）；錯密碼 → `401`。
- admin token 打受保護 admin 端點 → `200`；打 `/users/me` → `403`；user token 打 admin 端點 → `403`。
- admin token 走 `POST /auth/refresh` → `200` 且重簽後仍為 admin；`/auth/logout` → `204` 後失效。
- `POST /auth/logout-all` 以 admin 登入 → 撤該 admin 全部 refresh；user 的不受影響。
- 既有 `test_auth_api.py` / `test_refresh_api.py` 仍全綠（向後相容；注意 `sub` 由 user.id 改 principal_id，若既有測試斷言 sub 內容需同步）。

> conftest 加 `admin` fixture（比照 `alice`）+ seed helper；`alice`/`bob` 建立流程需經 principal（fixture 走 service，自動涵蓋）。

---

## 9. 實作順序（TDD 里程碑）

1. **Role enum** + **JWT role claim**（`create_access_token` / `extract_role`，向後相容）（8.1）。
2. **`principals` 表（`role` + `UNIQUE(id, role)`）+ PrincipalRepository**；`User` 加 `principal_id` + 常數 `role=0` 欄 + **複合 FK** + `CHECK(role=0)`（`is_active` 保留不動）；`UserRepository` 補 `get_by_principal_id`（get/list 維持原樣）；`models/__init__` 註冊。migration：建 principals + users 回填 `principal_id`（`users.is_active` 不動）。跑 **8.2 CASCADE + 複合 FK 錯配 IntegrityError 測試**。
3. **refresh_tokens 換擁有者**（`principal_id` FK→principals）：model + migration + repository（`revoke_all_for_principal`）+ `AuthService` 改用 principal + 既有測試改寫（8.3、8.4 部分）。
3.5. **交易邊界重構（Unit-of-Work，register/login 改 principal 的前置）**：repository 只 `flush` 不 `commit`；`UserService.create` 拆出不 commit 的建立路徑；由 use-case 方法持有唯一一次 commit。先補「register 中途失敗 → 資料庫零殘留」失敗測試（§8.4）再重構到綠。此步驟同時修掉既有 register 的三段 commit 非原子性（見 §5.4）。
4. **register/login 改走 principal**（sub=principal_id；**建立時給 `principal_id`、principal+user+identity+refresh 同一交易 commit**，見 §5.4）；順手補 login 的 `is_active` 檢查（§5.4/§8.4）；既有 auth 測試同步（8.4）。
5. **Admin model（含常數 `role=1` + 複合 FK + `CHECK(role=1)`）+ migration + AdminRepository + AdminService**（8.2）。
6. **Admin 登入**（共用 refresh 發放）+ `refresh` 依 principal.role 驗證重簽（8.4）。
7. **授權 dependencies**（`get_current_admin` / `require_role` / `get_current_principal`；`get_current_user` 加 role 檢查）（8.5）。
8. **Admin router** `/admin/auth/login` + 掛進 `api_router`；`logout-all` 改用 `get_current_principal`（8.6）。
9. **刪除改走 principal CASCADE**（`UserService.delete` / `AdminService.delete`）（8.2/8.4）。
10. **Seed script** + `.env.example`/config 初始 admin 變數。
11. **提交前檢查**：`ruff` / `ruff format` / `pyright` / `pytest` 全綠；真 MariaDB 驗 `alembic upgrade head` + `downgrade`；migration 回填後跑 §3.5 的「保留 id（`principal_id == id`）／無孤兒」驗證查詢，並確認 principals 的 AUTO_INCREMENT 已推到 `max(user.id)+1`。
12. **部署（見 §7 部署 checklist）**：部署程式碼＋migration（含保留 id 回填 + principals AUTO_INCREMENT 設到 `max(user.id)+1`）→ **不需輪替 `JWT_SECRET_KEY`**（保留 id 已消除 sub 碰撞）→ 例行監控。既有 access token 與 refresh token 皆不受影響，使用者無需重新登入。

---

## 10. 已定案決策

- ✅ 導入 **`principals` supertype**：User/Admin 各以 `principal_id`（unique FK, CASCADE）掛上；`role` 存於 principals。
- ✅ refresh token 擁有者 = `principal_id`（單一 FK + CASCADE）→ **保有完整性、無孤兒 token、刪除自動連帶**（best practice；取代無 FK 多型）。
- ✅ JWT `sub` = 全域 `principal_id`；`role` claim 快取角色；缺 claim = role 0（向後相容 + fail-safe）。
- ✅ `User`（role 0）/ `Admin`（role 1）兩張 child 表；Admin 自帶 `password_hash`（argon2），不走 Identity。
- ✅ **子型別-角色一致性以複合 FK 硬化（既定）**：`principals` 加 `UNIQUE(id, role)`，child 帶常數 `role` 欄 + `FK(principal_id, role)→principals(id, role)` + `CHECK(role=…)`，DB 層擋死錯配（defense-in-depth，對齊 integrity-first；見決策 D9）。
- ✅ **`is_active` 留在各 child（`users` / `admins` 各自一欄）**，不上移 principals：高頻已知型別讀取免 join、async 下結構上免疫 `MissingGreenlet`、對既有 `User`/refresh 模組改動最小。父表只放判別子 `role`；`email` / `name` 亦留 child（識別屬性需獨立命名空間）。代價：refresh 驗 active 多一次 child indexed lookup（低頻可忽略）。
- ✅ 授權以 dependency 表達，角色不符回 403；缺角色視為最低權限。
- ✅ admin 不公開註冊，經 seed script 佈建。
- ✅ **過渡期安全＝migration 保留 id（`principal.id == user.id`）**：既有 user 的 `principal_id` 沿用其 `user.id`，舊 access token 的 `sub` 仍解析到同一 user，杜絕帳號混淆；**cutover 不需輪替 `JWT_SECRET_KEY`、使用者無需重新登入**（見 §3.5、§7、決策 D8 修訂）。
- ✅ **交易邊界採 Unit-of-Work**：repository 只 flush、use-case 方法持有唯一 commit；principal+user+identity+refresh 原子落地，並修掉既有 register 三段 commit 的非原子性（見 §5.4、§9 step 3.5）。
- ✅ `login` 補齊 `is_active` 檢查，與 `admin_login` 語意一致（見 §5.4）。
- ✅ `principals.role` 不加獨立 index（低選擇性、無熱路徑查詢）；migration 回填採 set-based + 1:1 驗證（見 §3.2、§3.5）。
- ✅ **`get_current_principal` 不查 DB**：回輕量 `CurrentPrincipal(id, role)` 值物件（由已驗簽 token 取），logout-all 省一次 DB 往返（見 §5.6）。
- ✅ **`principals.role` 加 `CHECK(role IN (0,1))`**：父表 role 值域硬化，杜絕無對應 child 型別的 role，對齊 D9 integrity-first（見 §3.2/§3.5/§8.2）。
- ✅ **`extract_role` fail-safe**：缺 claim **或**未知值都退回最低權限 `Role.USER`（避免 `Role(2)` → 500），對齊 D6/D8（見 §5.1）。
- ✅ **access token TTL 維持全域 30 分（admin 不另縮短）**：有意識保留；殘留 gap＝`logout`/`logout-all` 只撤 refresh，live access token 存活到 exp 為止（≤30 分無法即時撤銷）。屬已接受的取捨；未來若 admin 面擴大再議 per-role TTL（見 §7 安全考量、§11）。
- ✅ 範圍＝角色基礎 + Admin 認證；不含 CMS 業務端點與 admin 管理 API。**admin 登入暴力破解防護（rate-limit/lockout）列為已知缺口，另立後續規格**（見 §7、§11）。

## 11. 待確認事項（Open Questions）

1. admin 的 refresh / logout 是否要另開 `/admin/auth/refresh`、`/admin/auth/logout` 鏡像端點（CMS 表面更分離），或沿用角色無關的 `/auth/*`？（暫定：沿用 `/auth/*`。）
2. seed script 放置位置與入口（`scripts/` vs `app/scripts/`；是否提供 uv script / Makefile 入口）。
3. ~~未來 RBAC：role 目前單一整數放 principals，若要 permission/scope 需否 `permissions` 表？~~ **已由 [`rbac.md`](./rbac.md) 接手**：型別內權限採**方案 A（等級 enum）**——`AdminRole`（SUPER_ADMIN/EDITOR/VIEWER）、`UserTier`，JWT 加 `grade` claim；權限層與 `principals.role`（型別判別子）分離。未來需細粒度 permission 再升方案 B。
4. admin 登入的 **rate-limit / 帳號鎖定**（複用 Redis）——本規格列為已知缺口，緊接一支後續規格；在此之前上線需於基礎設施層對 `/admin/auth/login` 限流（見 §7）。
5. ~~**principals supertype 的擴充假設是否成立**~~ **已定案：採 supertype**。專案路線圖已明確預期**未來會有第三種 principal-owned 身分**（partner / service account 等），故 D1 的擴充假設**成立並被接受為前提**——supertype（含 D9 複合 FK、role 三份冗餘）是正確的超前部署，而非為兩種身分過度設計。較輕的「兩個 nullable FK + CHECK」方案在「確定會擴充」前提下需反覆改結構，故不採（見決策 D1、D9）。

> ✅ 原 Open Q「是否加複合 FK 硬化」已定案：**採用**（見 §10、決策 D9）。
