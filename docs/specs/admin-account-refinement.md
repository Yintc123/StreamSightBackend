# 規格書：Admin 帳號欄位重整（移除 email、改 username 登入、封存／軟刪除、admin_role 等級欄）

> 🔄 **變更註記（後續演進，晚於本文）**：本文描述的 **seed script（`scripts/create_admin.py`）與 `initial_admin_password`（明文）／`initial_admin_name` 用於建立 DB 初始 admin 之部分已不適用**。現況:第一位 super admin 改為 **SSM-backed「初始 admin」**（`INITIAL_ADMIN_USERNAME` + `INITIAL_ADMIN_PASSWORD_HASH` 雜湊；**不進 DB**、哨兵 `principal_id=0`、恆 `super_admin`；`app/services/initial_admin.py`），登入後用它建立 DB admin;**seed 腳本已移除**。config 現為 `initial_admin_username` / `initial_admin_name`（可選顯示名）/ `initial_admin_password_hash`。下文凡提及「seed script／`initial_admin_password`（明文）」處以本註記為準（見 [`admin-management-service.md`](./admin-management-service.md) §3.7）。本文的 username 登入／封存軟刪除／`admin_role` 欄等其餘設計不受影響。

> 狀態：**已實作（✅ 547 tests 全綠，ruff / pyright 通過）** ／ 目標版本：next ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 🔗 依賴並延伸 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md)——本規格只調整既有 **`Admin` child 模型**與其認證／管理路徑，`principals` supertype、複合 FK、JWT role、refresh token 擁有者機制**一律沿用不動**。
>
> 🔗 **與 [`rbac.md`](./rbac.md)（Draft, next+1）的分工**：本規格**提前交付** `AdminRole` enum + `admins.admin_role` 欄位（含 `CHECK` 與**預設 `VIEWER`** fail-safe）+ `create`／seed 的等級設定（因本規格本就在改 admins 建表與 `create`，一次到位最省事）。`rbac.md` 仍負責**授權面**：JWT `grade` claim、`require_min_admin_role` 階梯授權、`user_tier`，以及 `set_role` 升降權業務入口。**⚠️ 交付後 `rbac.md` 需同步對齊**（見 §11 項 5：移除其重複的 admin_role 建欄／enum，並修正其對 admin 舊模型 email/is_active 的引用）。
>
> 📎 本文聚焦「怎麼做」（資料模型／介面／流程／測試計畫）。牽涉「為什麼」的取捨在 §2 摘要，重大決策彙整於 §10。

---

## 1. 背景與目標

現有 `Admin`（CMS 管理者，role=1）以 **email + 密碼**登入，且僅有 `is_active` 布林旗標可停用帳號，刪除走 **物理刪除**（刪 `principals` 該列 → CASCADE 連帶清 admin + refresh_tokens）。

實務上 CMS 管理者：

- **不需要 email**：admin 是後台內部帳號、由 seed／管理者佈建，不做 email 驗證／找回密碼，email 屬多餘且徒增加密欄位維護成本。
- **登入識別改用 `username`**：後台帳號慣以「使用者名稱」登入，語意直觀且非 PII。
- **需要「封存（archive）」與「軟刪除（delete）」**：管理者停用／刪除應**可回溯、可復原、保留發生時間**，而非直接物理抹除（利於稽核與誤刪救援）。

### 目標

- `Admin` model：**移除 `email`** 欄位；**新增 `username`**（唯一、非加密）作為登入識別。
- `Admin` model：**新增 `archived_at` / `deleted_at`**（nullable `DateTime(timezone=True)`）表達封存與軟刪除狀態，**並各配一稽核操作者欄 `archived_by` / `deleted_by`**（nullable `FK → principals.id`，`ondelete=SET NULL`，成對寫入）。
- **`is_active` 由「實體欄位」改為「計算屬性」**：`is_active == (archived_at IS NULL AND deleted_at IS NULL)`。如此**登入／refresh／授權**的既有 `not child.is_active` 判斷**零改動**即自然涵蓋「封存」與「軟刪除」皆不可用。
- Admin 登入改吃 `username + password`（新增 `AdminLoginRequest`）。
- `AdminService`：新增 `archive` / `unarchive` / `restore`；`delete` **由物理刪除改為軟刪除**（設 `deleted_at`），封存與軟刪除都**連帶撤銷該 principal 的 refresh token**。
- Repository / DTO / seed script / config 同步把 email 改成 username；新增共用 `normalize_username`（`app/core/security.py`）。
- **新增 `AdminRole` enum + `admins.admin_role` 欄位**（`String(20)` + `CHECK`，**預設 `VIEWER`** 最低權限 fail-safe）；`create` 可帶等級（預設 `VIEWER`），**seed 初始 admin 給 `SUPER_ADMIN`**。JWT `grade` claim 與階梯授權**不在本規格**（屬 `rbac.md`，見上方分工）。
- **修訂原始 migration `c3d4e5f6a7b8`**（該表未部署 → 就地改建表，不新增 add-then-remove revision，見 §3.2）。

### 非目標（Out of scope）

- Admin 管理 API（列出 / 建立 / 封存 / 刪除 / 復原 admin 的 CMS 端點）——沿用既有規格 §1 非目標，另立規格；本規格只改 **model / service / 認證** 層，讓管理 API 有正確地基。
- **purge（永久清除）**：本專案**不採**，只做軟刪除（§2.6）；軟刪除的 username 永久保留、admin 列無 PII。僅未來確有「同名重用」或法遵硬刪除需求才引入。
- **HTTP 管理端點**：`archive` / `unarchive` / `restore` / `delete` 本規格只實作到 **service 層**（含測試），不開 CMS 端點——留待 admin 管理 API 規格。
- User（role=0）的任何改動：User 保留其 `is_active` 實體欄位與現行刪除語意，**完全不動**。本規格 blast radius 僅限 `admins`。
- User 端登入的時序側通道修正（僅修 admin 端，見 §5.4／§7）。
- **admin_role 的授權面**：JWT `grade` claim、`require_min_admin_role` 階梯授權、`set_role` 升降權入口、`user_tier`——**皆屬 [`rbac.md`](./rbac.md)**（本規格只交付 enum + 欄位 + 建立時預設，不含任何「用 admin_role 做授權判斷」的邏輯）。

---

## 2. 設計決策

### 2.1 `username` 取代 `email` 作登入識別（非加密、明文唯一索引）

- 移除 `email`（`DeterministicEncryptedString`）；新增 `username: String(100)`，`unique=True, index=True`。
- **不加密**：username 是後台自訂登入代號、非 PII，無 `email` 的個資顧慮，故用一般 `String` + 原生唯一索引即可（比加密欄位更簡單，唯一／查詢天然可用）。連帶 `Admin` model **不再需要** `DeterministicEncryptedString` 與 module 級 `_ENCRYPTION_KEY`。
- **正規化（單一入口）**：為避免 `Admin` / `admin` 視為兩個帳號，於 `app/core/security.py` 新增共用純函式 **`normalize_username(raw) -> str`（`raw.strip().lower()`）**，由 DTO validator（`AdminLoginRequest`）、`AdminService.create`、seed **共同呼叫**（DRY、單一事實來源）。唯一約束建立在正規化後的值上（見 §5.1/§5.3/§7）。
- **格式限制（建立時）**：username 為登入識別，於**建立路徑**強制 `^[a-z0-9._-]+$`（小寫英數與 `._-`）、長度 3–100，擋掉空白／同形字／看似 email 的值（best practice：登入識別衛生）。**登入路徑只正規化、不硬驗格式**——格式不符的登入嘗試單純查無此帳號 → 統一 401，避免用 422 洩漏「此格式帳號不存在」（見 §5.4）。

### 2.2 封存與軟刪除＝nullable 時間戳（何時）＋ 稽核操作者（誰）

- `archived_at` / `deleted_at` 皆 `Mapped[datetime | None]`（`DateTime(timezone=True)`, nullable，預設 `None`）。`NULL` = 未發生；有值 = 發生的時間點。相較布林，**保留「何時封存／刪除」的稽核資訊**，且與既有 `refresh_tokens.revoked_at`（同型別 nullable timestamp）風格一致。
- **操作者稽核 `archived_by` / `deleted_by`**：各一 nullable `FK → principals.id`（`ondelete="SET NULL"`——**purge 掉操作者不應連帶抹掉被稽核列**），記「誰做的」。admin 生命週期是高稽核場景，「何時＋誰」才是 audit-grade。
  > **成對不變式**：`archived_at IS NULL ⟺ archived_by IS NULL`（`deleted_*` 同理）——設封存／刪除時**同時**寫時間與操作者，`unarchive`/`restore` 時**同時**清兩者。
  > **現況**：本規格 service 方法接受**可選** `actor_principal_id`（seed／script 無操作者 → 傳 `None` → 欄位為 NULL）；待 admin 管理 API 上線、有已認證的操作 admin 時，端點把 `current_admin.principal_id` 傳入，欄位自然填實。**欄位與寫入路徑先備妥、前向相容**。

### 2.3 `is_active` 改為計算屬性（承接封存＋軟刪除，讓認證路徑零改動）

移除 `admins.is_active` **實體欄位**，改在 `Admin` 上提供**計算屬性**：

```python
@property
def is_active(self) -> bool:
    """封存或軟刪除皆視為「不可用」。登入／refresh／授權共用此語意。"""
    return self.archived_at is None and self.deleted_at is None
```

- **關鍵好處**：既有共用 refresh 路徑（`_load_child` 後 `if child is None or not child.is_active`）與 `get_current_admin` 的 `not admin.is_active` 判斷**一行都不用改**——它們讀的是屬性，而 `archived_at` / `deleted_at` 是 `admins` 本地欄位，讀取即讀已載入那一列，**async 下結構上免疫 `MissingGreenlet`、免 join**（延續 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md) §3.2「本地欄位」原則）。
- **User 不受影響**：`User` 仍是**實體** `is_active` 欄位；共用路徑對 user 讀實體欄、對 admin 讀計算屬性，介面一致、語意一致。

### 2.4 狀態機（active / archived / deleted）

| 狀態 | 條件 | 可登入 / refresh / 授權 | 說明 |
|---|---|---|---|
| **active** | `archived_at IS NULL` 且 `deleted_at IS NULL` | ✅ | 正常帳號 |
| **archived（封存）** | `archived_at IS NOT NULL` 且 `deleted_at IS NULL` | ❌ | 停用但可 `unarchive` 復原 |
| **deleted（軟刪除）** | `deleted_at IS NOT NULL` | ❌ | 資料保留、可 `restore` 復原；**本專案不做 purge**（軟刪除為最終清除機制，見 §2.6） |

- `deleted_at` 優先於 `archived_at`（軟刪除為終態）。`is_active` 對三者的判定與上表一致。
- 轉移（service 方法，見 §5.3；**本規格只到 service 層、無 HTTP 端點**）：`archive`（active→archived）、`unarchive`（archived→active）、`delete`（active/archived→deleted）、`restore`（deleted→active）。對已 `deleted` 的 admin 做 `archive`/`unarchive`/再 `delete` → `NotFoundError`（`get` 預設過濾軟刪除）；`restore` 走 `get(include_deleted=True)`。

### 2.5 封存／軟刪除連帶撤銷 refresh token；access token 沿用既有殘留窗口取捨

- `archive` 與 `delete` 都呼叫 `RefreshTokenRepository.revoke_all_for_principal(principal_id, datetime.now(UTC))`（**注意實際簽名有 `revoked_at` 第二參數**，見 §5.3），使被封存／刪除的 admin **無法再 refresh**（既有 access token 仍存活至 `exp`，≤30 分——與 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md) §7「無狀態 access token 撤銷窗口」相同、已接受的取捨）。
- **軟刪除不再刪 `principals`**：故不觸發 CASCADE，refresh_tokens 的清除改由**明確撤銷（revoke）**達成——語意是「撤銷」而非「物理刪除」，與軟刪除一致（資料保留、可稽核）。

### 2.6 軟刪除的 username 唯一性＝永久保留（本專案不做 purge，已定案）

軟刪除保留該列 → 其 `username` 仍佔用唯一索引，**無法以同一 username 建立新 admin**（`ConflictError`）。已評估三種「釋放名稱」替代方案，**皆劣於保留**，故定案為**永久保留**：

| 方案 | 為何不採 |
|---|---|
| **部分唯一索引** `WHERE deleted_at IS NULL` | MariaDB 不支援 partial index，不可攜（違反本專案 SQLite/MariaDB 雙跑前提）。 |
| **組合唯一** `UNIQUE(username, deleted_at)` | MySQL/MariaDB 視 NULL 為互異 → 兩筆 active（`deleted_at` 皆 NULL）的同 username 竟**不被擋**，反而破壞 active 唯一性。錯誤解。 |
| **軟刪除時改名**（`username = f"{username}#del{id}"`） | 竄改登入識別本身、損失稽核原值；且高權限 admin 身分不宜在刪除時被系統改寫。 |

保留語意對後台場景無礙（admin 稀少、名稱長期保留可接受）。**本專案決定只做軟刪除、不做 purge（永久清除）**，故軟刪除的 username＝**永久保留**（此為明確接受的取捨，非待實作缺口）。誤刪以 `restore` 復原（同列、名稱本就在）。integrity-first：唯一索引維持單一欄、DB 層強制，最單純可靠。
> 若**未來**確有「釋放同名重用」或法遵硬刪除需求，才引入 purge（物理刪 principal→CASCADE）；目前不在藍圖。

### 2.7 `admin_role` 權限等級欄（預設 `VIEWER`，seed 給 `SUPER_ADMIN`）

新增 `admins.admin_role`（`SUPER_ADMIN` / `EDITOR` / `VIEWER`，權限高→低的**有序階梯**），對齊 [`rbac.md`](./rbac.md) 方案 A。**本規格只交付資料模型與建立時的預設**：

- **與型別判別子 `role` 完全不同**：`admins` 建立後會有**兩個易混淆的 role 欄**——`role`（`SmallInteger`，常數 `1`）＝ principal **型別判別子**（「這是 admin」），被複合 FK + `CHECK(role=1)` 釘死、不可變；`admin_role`（`String(20)`）＝ **權限等級**（「這個 admin 能做多少事」），可變、供授權。實作時**兩欄都要加註解**避免誤用。
- **存字串 + `CHECK` 值域硬化**：`admin_role` 存 enum 的字串值（`'super_admin'`/`'editor'`/`'viewer'`），DB 加 `CHECK(admin_role IN (...))`（比照 `role` 存 SmallInteger + CHECK 的 integrity-first 風格）。程式端以 `AdminRole(admin.admin_role)` 包裝讀取。等級是 child **本地欄位**，未來授權讀它**免 join、免 async footgun**（同 `is_active`）。
- **預設 `VIEWER`（fail-safe 最低權限）**：`mapped_column(default=..., server_default=...)` 皆為 `VIEWER`；`create` 的 `admin_role` 參數預設 `VIEWER`。**新建 admin 一律最低權限**，需明確指定才升權——杜絕「忘了設等級 → 意外拿到高權限」。
- **seed 例外給 `SUPER_ADMIN`**：初始 bootstrap admin 必須能管理其他 admin，故 seed script 以 `AdminRole.SUPER_ADMIN` 建立（見 §4）。否則全體 admin 都停在 `VIEWER`、無人能升權。
- **範圍邊界**：本規格**不含**任何「讀 `admin_role` 做授權」的邏輯（`grade` claim、`require_min_admin_role`、`set_role`）——那些是 [`rbac.md`](./rbac.md) 的職責。此處交付後，`admin_role` 欄已就緒、預設安全，rbac.md 只需接手授權面（並移除其重複建欄，見 §11 項 5）。

---

## 3. 資料模型

### 3.1 `AdminRole` enum（`app/core/enums.py`，新增）與 `Admin` model（改）

**`AdminRole`（新增於 `app/core/enums.py`，與 `Role` 並列）**——對外是字串（前端可讀、自我描述），用 `StrEnum`；命名**刻意避開 `USER`/`ADMIN`**（已被型別判別子 `Role` 佔用）：

```python
class AdminRole(StrEnum):
    """Admin 型別內的權限等級（有序階梯，高→低）。存於 admins.admin_role。

    僅為權限等級，與 principals 的型別判別子 Role（USER/ADMIN）不同層次。
    授權階梯（require_min_admin_role）與 grade claim 見 docs/specs/rbac.md。
    """

    SUPER_ADMIN = "super_admin"  # 全權，含管理其他 admin
    EDITOR = "editor"           # 內容編輯
    VIEWER = "viewer"           # 唯讀（最低權限，建立預設）
```

**`Admin` model（`app/models/admin.py`）**：

```python
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import AdminRole


class Admin(Base):
    __tablename__ = "admins"

    # 一對一掛上 principals（複合 FK 承擔參照）——不變
    principal_id: Mapped[int] = mapped_column(unique=True, index=True)
    # 常數判別欄：Admin 永遠 role=1（複合 FK + CHECK 釘死）——不變。
    # ⚠️ 這是「型別判別子」（帳號是 admin），與下方 admin_role（權限等級）完全不同，勿混淆。
    role: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))

    # 【新增】登入識別：非加密、唯一索引；service 層正規化為小寫後儲存（見 §5.3）
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))  # argon2id

    # 【新增】權限等級（可變，供 rbac 授權）。⚠️ 與上方常數 role 判別子不同層次（見 §2.7）。
    # 存字串值 + CHECK 硬化；預設 VIEWER（最低權限 fail-safe，seed 例外給 super_admin）。
    admin_role: Mapped[str] = mapped_column(
        String(20),
        default=AdminRole.VIEWER.value,
        server_default=AdminRole.VIEWER.value,
    )

    # 【新增】封存 / 軟刪除時間戳（NULL = 未發生）＋ 操作者稽核（誰做的）
    # 成對不變式：archived_at 與 archived_by 同進同退（deleted_* 同理），見 §2.2。
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    archived_by: Mapped[int | None] = mapped_column(
        ForeignKey("principals.id", ondelete="SET NULL"), nullable=True, default=None
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    deleted_by: Mapped[int | None] = mapped_column(
        ForeignKey("principals.id", ondelete="SET NULL"), nullable=True, default=None
    )

    # 【移除】email（DeterministicEncryptedString）、_ENCRYPTION_KEY、DeterministicEncryptedString import
    # 【移除】is_active 實體欄位 → 改為下方計算屬性

    __table_args__ = (
        ForeignKeyConstraint(
            ["principal_id", "role"],
            ["principals.id", "principals.role"],
            ondelete="CASCADE",
            name="fk_admins_principal_role",
        ),
        CheckConstraint("role = 1", name="ck_admins_role_admin"),
        CheckConstraint(
            "admin_role IN ('super_admin', 'editor', 'viewer')",
            name="ck_admins_admin_role",
        ),
    )

    @property
    def is_active(self) -> bool:
        """封存或軟刪除皆視為不可用（登入／refresh／授權共用；見 §2.3）。"""
        return self.archived_at is None and self.deleted_at is None

    def __repr__(self) -> str:
        return f"<Admin id={self.id} username={self.username!r}>"
```

> `id` / `created_at` / `updated_at` 由 `Base` 提供，不變。`principal_id` / `role` / 複合 FK / `ck_admins_role_admin` 一律不動。
>
> **兩個 role 欄的用途對照（實作務必註解，見 §2.7）**：`role`（SmallInteger 常數 1）＝型別判別子、不可變；`admin_role`（String）＝權限等級、可變、預設 `viewer`。

### 3.2 Migration：**修訂原始 `c3d4e5f6a7b8`**（不新增 add-then-remove revision）

**最佳實踐定案**：目前 alembic head 正是「新增 admins 表」的 `c3d4e5f6a7b8`，且它與整個 Admin 功能都只存在於**未合併、未部署**的 `feat/refresh-token-rotation` 分支——`email` 欄位是**同一批未上線**的產物。此時「新增一支把剛加的 `email` 立刻 drop、再補 username／時間戳」的 revision 會產生一個**現實中從未存在過的中間 schema**、並讓歷史充滿自我抵銷的雜訊。

> **原則**：「migration 一旦提交即不可變」只適用於**已部署／已共享**的 revision。對**尚未上線的 feature 分支 schema**，最佳實踐是**就地修訂／壓平（squash）**，讓 schema 從一開始就自洽。故本規格**直接修訂 `c3d4e5f6a7b8` 的 `upgrade()`／`downgrade()` 與 `Admin` model**，**不新增 revision**。

**修訂 `alembic/versions/c3d4e5f6a7b8_add_admins_table.py` 的 `create_table("admins", ...)`**：

- **移除**：`email` 欄與 `op.create_index("ix_admins_email", ...)`；`is_active` 欄與其 `bool_true` server_default 邏輯。
- **新增欄位**：
  ```python
  sa.Column("username", sa.String(length=100), nullable=False),
  sa.Column(
      "admin_role", sa.String(length=20), nullable=False, server_default="viewer"
  ),
  sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
  sa.Column(
      "archived_by", sa.Integer(),
      sa.ForeignKey("principals.id", ondelete="SET NULL"), nullable=True,
  ),
  sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
  sa.Column(
      "deleted_by", sa.Integer(),
      sa.ForeignKey("principals.id", ondelete="SET NULL"), nullable=True,
  ),
  ```
- **新增約束／索引**：把 `op.create_index("ix_admins_email", ...)` 換成 `op.create_index("ix_admins_username", "admins", ["username"], unique=True)`；並在 `create_table` 的約束串中加 `sa.CheckConstraint("admin_role IN ('super_admin', 'editor', 'viewer')", name="ck_admins_admin_role")`。
- `admins` 無既有資料（seed 建立）→ **無需回填**；新列 `admin_role` 由 `server_default='viewer'` 自動取最低權限，seed 另以 `SUPER_ADMIN` 建立（見 §4）。
- `principal_id` / `role` / 複合 FK / `CHECK(role=1)` / 時間戳欄一律不動；`downgrade()` 仍是 `drop_table("admins")`（無需改）。

> **前提（此決策的成立條件）**：「就地修訂」best practice **成立於「該 revision 未部署、且分支未被多位開發者共用」**。目前 `c3d4e5f6a7b8` 僅在未合併的 feature 分支、屬單一工作流 → 修訂安全。**若此分支已被多人 pull 並各自 `upgrade`**，修訂 migration 會造成他人 DB 與新版不一致 → 那種情境應改回 §3.2 末的 append-only 備援路線。
>
> **開發者本機再同步（唯一操作注意事項）**：若你**已**對本機 MariaDB 跑過 `alembic upgrade head`（舊版 c3d4e5f6a7b8 已套用），修訂後 alembic 不會自動重跑同一 revision。請先 `alembic downgrade b2c3d4e5f6a7`（或重建 dev DB）再 `alembic upgrade head`，讓修訂後的建表生效。測試環境走 SQLite `create_all`、不經 migration，直接反映 model，無此問題。
>
> ⚠️ 修訂後在真 MariaDB 跑 `upgrade head` / `downgrade base` 驗證一次。

> **備援（若日後 admins 已部署才要再改）**：屆時 c3d4e5f6a7b8 已不可變，才回到「新增 ALTER revision（drop email/is_active、add username 回填 + 時間戳）」的 append-only 路線。本規格因該表未上線而不採此路。

---

## 4. 設定與 Seed

- `.env.example`：`INITIAL_ADMIN_EMAIL` → **`INITIAL_ADMIN_USERNAME`**；新增 `INITIAL_ADMIN_NAME`（顯示名稱，可選，預設等於 username）。
  ```bash
  # 初始 CMS admin（供 seed script 建立；建立後可移除）
  INITIAL_ADMIN_USERNAME=admin
  INITIAL_ADMIN_NAME=Administrator
  INITIAL_ADMIN_PASSWORD=change-me-strong-password
  ```
- `BaseAppSettings`（`app/core/config/base.py:98`）：`initial_admin_email` → `initial_admin_username: str = ""`；新增 `initial_admin_name: str = ""`（空則 seed 時以 username 當 name）。`initial_admin_password` 不變。
- `scripts/create_admin.py`：現為 `create_initial_admin(session, email, password)` 內部呼叫 `service.get_by_email` + `service.create(email=..., name="admin", password=...)`。改為 `create_initial_admin(session, username, name, password)`：冪等改以 **`AdminService.get_by_username`** 判斷是否已存在，**`create(username=..., name=name or username, password=..., admin_role=AdminRole.SUPER_ADMIN)`**（bootstrap admin 需能管理其他 admin，故非預設的 `VIEWER`，見 §2.7）；`main()` 改讀 `settings.initial_admin_username` / `settings.initial_admin_name`。

---

## 5. 介面設計

### 5.1 DTO / schema

- **新增** `AdminLoginRequest`（`app/dtos/auth.py`，並於 `app/dtos/__init__.py` 匯出）：**必須新增、不能沿用 `LoginRequest`**——`LoginRequest.email` 型別是 `EmailStr`，會在 pydantic 驗證階段直接擋掉非 email 格式的 username。以 `field_validator` 在 DTO 邊界就正規化（單一入口，見 §2.1）：
  ```python
  from pydantic import field_validator
  from app.core.security import normalize_username

  class AdminLoginRequest(BaseModel):
      """Payload for POST /admin/auth/login."""
      username: str = Field(min_length=1, max_length=100, description="Admin username")
      password: str = Field(description="Plain password")

      @field_validator("username")
      @classmethod
      def _normalize(cls, v: str) -> str:
          return normalize_username(v)  # strip + lower（登入只正規化、不硬驗格式，見 §2.1/§5.4）
  ```
- **改** `AdminResponse`（`app/api/routers/admin/schemas.py`）：移除 `email`，改為 **`id / username / name / admin_role`**：
  ```python
  from app.core.enums import AdminRole

  class AdminResponse(BaseModel):
      model_config = ConfigDict(from_attributes=True)
      id: int
      username: str
      name: str
      admin_role: AdminRole    # 前端據此渲染 CMS 選單／按鈕（權威來源＝/me）
  ```
  - **含 `admin_role`**：`/admin/me` 是 admin 讀取自身，其權限等級**對前端有意義**（依等級顯示/隱藏 CMS 功能）；rbac.md 亦以 `/me` 為等級的權威來源。`from_attributes` 讀 `admin.admin_role`（str）→ pydantic 收斂為 `AdminRole` → 序列化為 `"viewer"` 等字串。
  - **不含狀態時間戳**：`/admin/me` 的 admin **恆為 active**（archived/deleted 者無法通過授權），`archived_at`/`deleted_at` 恆 null／無意義 → 不放（purposeful DTO）。未來 admin 管理**列表**端點需顯示封存／刪除狀態時，另立 `AdminSummary`（含 `is_active` / `archived_at` / `deleted_at`）於管理 API 規格。
- `TokenPayload` / `TokenResponse` 不變。

### 5.2 Repository（`app/repositories/admin.py`）

- **移除** `get_by_email`，**新增** `get_by_username(username: str) -> Admin | None`（一般 `WHERE username == ...`，明文比對）。
- `get_by_principal_id` 不變。
- **查詢語意**：repository 維持「dumb」——回傳該列即可（含已封存／軟刪除者），由 **service／dependency 讀 `is_active` 計算屬性判定可用性**（延續既有做法，見 §2.3）。`AdminService.get` 預設過濾軟刪除（見 §5.3）。
  > ⚠️ **`is_active` 是 Python 計算屬性、不可進 SQL `WHERE`**。本規格的單列 `get` 在 Python 判定即可；但**未來 admin 管理列表**端點要「列出 active／封存／已刪」時，**必須用時間戳謂詞**（`WHERE archived_at IS NULL AND deleted_at IS NULL` 等），屆時於 repository 新增 `list_active()` / `list_archived()` 等**以欄位查詢**的方法，勿嘗試對計算屬性下 filter。

### 5.3 Service（`app/services/admin.py`）

建構子注入新增 `RefreshTokenRepository`（供封存／軟刪除撤 token）：`self.refresh_repo = RefreshTokenRepository(session)`（現有 `AdminService.__init__` 只有 `repo` + `principal_repo`，需補這一個）。

> **時間來源**：以 `from datetime import UTC, datetime` 的 **`datetime.now(UTC)`**（aware-UTC）設定 `archived_at` / `deleted_at`，與 `AuthService`／`RefreshTokenRepository` 全庫慣例一致，勿用 naive `datetime.now()`。（`auth.py` 的 `_as_utc` 是「讀取 DB 回來的 naive datetime 正規化」用途，非產生時間點用，勿誤用。）

- **正規化／格式**：共用 `normalize_username`（`app/core/security.py`，見 §2.1），`AuthService.admin_login`、`create`、seed 皆呼叫它，不各自實作。`create` 另以 module 常數 `_USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,100}$")` 驗**正規化後**的值，不符拋 **`BadRequestError`**（`app/core/exceptions` 既有，對映 400；**勿手拋 pydantic `ValidationError`**——那需 model 脈絡、且語意是「DTO 驗證」而非「業務規則」）——格式限制只在**建立**路徑強制（見 §2.1）。
- **`create(username, name, password, admin_role: AdminRole = AdminRole.VIEWER)`**（取代 `create(email, name, password)`）：`u = normalize_username(username)` → 驗 `_USERNAME_RE` → 查重（`get_by_username(u)`）重複則 `ConflictError` → argon2 hash → 建 `Principal(role=ADMIN)` + `Admin(username=u, name=..., password_hash=..., admin_role=admin_role.value)`，**同一交易**落地（Unit-of-Work 不變）。**`admin_role` 預設 `VIEWER`（最低權限 fail-safe）**；seed 傳 `SUPER_ADMIN`（見 §4）。log 直接記 username（非 PII，**不再用 `mask_email`**）。
  > `set_role`（升降權）不在本規格——屬 rbac.md（見 §2.7 範圍邊界、§11 項 5）。
- **`get(admin_id, *, include_deleted=False) -> Admin`**：預設 `deleted_at IS NOT NULL` 視為 `NotFoundError`（軟刪除對一般讀取等同不存在）；管理／復原情境傳 `include_deleted=True`。
- `get_by_username` / `get_by_principal_id`：委派 repository（回原列，可用性由呼叫端判定）。
> **操作者稽核**：`archive` / `delete` 均帶**可選** `actor_principal_id: int | None = None`（呼叫端有已認證 admin 時傳入，seed／script 傳 None）；寫入時**成對**設 `archived_by`/`deleted_by`（見 §2.2 不變式），`unarchive`/`restore` **成對**清回 None。

- **`archive(admin_id, *, actor_principal_id=None) -> Admin`**：`get`（未刪除）→ 若已 `archived_at` 則 idempotent 直接回 → 設 `archived_at = datetime.now(UTC)`、`archived_by = actor_principal_id` → **`revoke_all_for_principal(principal_id, datetime.now(UTC))`** → 單一 commit。
- **`unarchive(admin_id) -> Admin`**：`get` → 清 `archived_at = None`、`archived_by = None`（成對）→ commit（不自動復發 token，使用者需重新登入）。
- **`restore(admin_id) -> Admin`**（與 `delete` 對稱，補完狀態機）：`get(include_deleted=True)` → 若 `deleted_at is None` 直接回（idempotent）→ 清 `deleted_at = None`、`deleted_by = None`（成對）→ commit。**同名保留至此才可復原**（同一列，username 未被他人佔用）；不自動復發 token。無 HTTP 端點（service 層 + 測試即可，見 §1 非目標）。
- **`delete(admin_id, *, actor_principal_id=None) -> None`**（**改為軟刪除**）：`get`（未刪除）→ 設 `deleted_at = datetime.now(UTC)`、`deleted_by = actor_principal_id` → **`revoke_all_for_principal(principal_id, datetime.now(UTC))`** → 單一 commit。**不再刪 `principals`、不再觸發 CASCADE**（見 §2.5）。
  > ⚠️ `RefreshTokenRepository.revoke_all_for_principal(principal_id, revoked_at)` **有兩個參數**（回傳撤銷筆數）——第二個 `revoked_at` 必填，勿只傳 principal_id。

### 5.4 認證流程（`app/services/auth.py`）

- **`admin_login(payload: AdminLoginRequest)`**（原吃 `LoginRequest`，見 `auth.py:175`）：`admin_repo.get_by_username(payload.username)`（DTO 已正規化）取 admin → **常數時間驗證**（見下）→ **`if admin is None or 密碼不符 or not admin.is_active`**（涵蓋不存在／密碼錯／封存／軟刪除，統一模糊訊息 `UnauthorizedError`）→ 簽 `create_access_token(admin.principal_id, Role.ADMIN)` + refresh。log 記 username（非 `mask_email`）。
  > **時序側通道修正（best practice，本規格順修 admin 端）**：既有寫法 `if admin is None or not await verify_password(...)` 在 `admin is None` 時**短路跳過 argon2 verify** → 回應時間洩漏帳號是否存在（見 §7）。改為**無論帳號是否存在都跑一次 argon2**，拉平兩分支時間。
  >
  > **實作為共用 primitive（放 `app/core/auth/password.py`，與 `_password_hasher` 同檔）**——非塞進 `admin_login`，好讓 user 端日後一行接上：
  > ```python
  > # ⚠️ 不可用 async 的 hash_password（module 頂層不能 await）；用既有的同步 _password_hasher 算一次
  > _DUMMY_PASSWORD_HASH: str = _password_hasher.hash("dummy-for-constant-time")
  >
  > async def verify_password_or_dummy(stored_hash: str | None, plain: str) -> bool:
  >     """帳號存在→驗真 hash；不存在→驗 dummy（必 False），拉平時序、防列舉。"""
  >     return await verify_password(plain, stored_hash if stored_hash is not None else _DUMMY_PASSWORD_HASH)
  > ```
  > `admin_login` 改呼叫 `await verify_password_or_dummy(admin.password_hash if admin else None, payload.password)`。此為 admin（高價值目標）的強化；**user 端 `login` 同缺口留後續，但屆時直接複用同一 helper**（見 §1 非目標、§11 項 3）。
  >
  > 其餘只需：`get_by_email`→`get_by_username`、`mask_email(admin.email)`→`admin.username`、參數型別 `LoginRequest`→`AdminLoginRequest`；`is_active` 因改計算屬性，封存／軟刪除**自動被 `not admin.is_active` 擋下、判斷式不需改**。
- **`refresh`**：共用路徑 `_load_active_child`（`auth.py:255`）後 `if child is None or not child.is_active`（`auth.py:236`）**零改動**即擋掉封存／軟刪除的 admin（讀計算屬性）；軟刪除後 `principals` 列仍在，故 `principal_repo.get(...)` 非 None、續由 child 的 `is_active` 擋下。
- `login`（user 側）、`logout`、`logout_all`：**不動**。

### 5.5 授權 dependency

- 真正的 admin `is_active` 檢查在 **`AuthService.get_admin_from_token`（`auth.py:321-336`，第 333 行 `if admin is None or not admin.is_active`）**；`app/api/dependencies/auth.py` 的 `get_current_admin` 只是薄封裝呼叫它。**兩者皆零改動**——計算屬性自動涵蓋封存／軟刪除（`get_by_principal_id` 對軟刪除者仍回列，`is_active` 計算為 False → 401 `Admin not found or inactive`）。

### 5.6 Router（`app/api/routers/admin/router.py`）

- `POST /admin/auth/login`：改吃 `AdminLoginRequest`（呼叫 `service.admin_login`）。
- `GET /admin/me`：回 `AdminResponse`（`id/username/name/admin_role`），邏輯不變（登入者恆為 active）。
- **不新增** admin 管理端點（`archive`/`unarchive`/`restore`/`delete` 只到 service 層；HTTP 端點屬 admin 管理 API，見 §1 非目標與 §11）。

---

## 6. 流程圖

```
建立（交易內兩步；admin_role 預設 viewer，seed 傳 super_admin）：
  create(..., admin_role=VIEWER) ─► normalize(username) ─► 驗格式 ─► 查重 ─► principals.create(role=1)
                                 ─► admins(username, name, password_hash, admin_role)

登入（常數時間：帳號不存在也跑一次 argon2）：
  POST /admin/auth/login {username, password}   # DTO 已 normalize(username)
    ─► get_by_username ─► verify_password(admin.hash or _DUMMY_HASH) ─► is_active?(archived_at/deleted_at 皆 NULL)
       ├─ 否 ─► 401（統一模糊訊息）
       └─ 是 ─► access{sub=principal_id, role=1} + refresh

狀態機（service 層，無 HTTP 端點）：
  archive ─► set archived_at=now ─► revoke_all_for_principal      unarchive ─► clear archived_at
  delete  ─► set deleted_at=now  ─► revoke_all_for_principal      restore   ─► clear deleted_at
           （軟刪除；不刪 principals、不 CASCADE；username 永久保留）

授權 / refresh（零改動，讀計算屬性 is_active）：
  get_current_admin / refresh ─► not admin.is_active ─► 擋下（封存或軟刪除皆然）
```

---

## 7. 安全性考量

- **登入識別非 PII**：username 明文儲存無個資外洩顧慮；密碼仍 argon2id、統一模糊錯誤訊息防列舉。
- **大小寫正規化**：登入與建立皆 `lower()`，避免 `Admin`/`admin` 繞過唯一性或造成重複帳號。此為**跨庫一致性**關鍵：MariaDB 預設定序多為 case-insensitive（`utf8mb4_*_ci`，DB 層即擋 `Admin`=`admin`），而測試用 SQLite 的 `TEXT` 唯一約束是 case-sensitive——**只有在 app 層正規化，兩種 DB 行為才一致**（否則 SQLite 綠、MariaDB 紅）。
- **封存／軟刪除即時性**：兩者都撤銷 refresh token；access token 為無狀態 JWT，殘留至 `exp`（≤30 分）——延續 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md) §7 已接受的取捨，未變更。
- **軟刪除資料保留**：`deleted_at` 保留列與稽核痕跡。本專案**只做軟刪除、不做 purge**（§2.6）；admin 列已**不含 PII**（email 已移除，僅 username／name），法遵硬刪除壓力低。若未來確有法遵硬刪除需求，才引入 purge。
- **admin 登入暴力破解**：仍為既有已知缺口（無 rate-limit / lockout），本規格未改變此現況，見 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md) §7 / §11。
- **登入時序側通道（admin 端本規格已修）**：`admin_login` 改為**無論帳號是否存在都跑一次 argon2 verify**（None → 對 `_DUMMY_PASSWORD_HASH`），消除「短路跳過 verify」的時序洩漏（見 §5.4）。**user 端 `login` 仍有同缺口**，因僅限 admin 範圍，另立後續處理（見 §1 非目標、§11 後續建議）。
- **username 唯一性保留**：軟刪除者的 username **永久保留、不可重用**（本專案不做 purge，§2.6 已定案），屬功能語意而非安全風險；誤刪以 `restore` 復原（同列、名稱本就在）。

---

## 8. TDD 測試計畫（先寫、先看到 RED）

> 由內而外：model / repository → service（登入 / 封存 / 刪除）→ dependency / API。既有 admin 測試中所有 `email=` 佈局改為 `username=`。

### 8.1 Unit — Model / Repository / util（`tests/unit/`）
- `Admin.is_active` 計算屬性：`archived_at`/`deleted_at` 皆 None → True；任一有值 → False；兩者皆有值 → False。
- `AdminRepository.get_by_username` 命中 / 未命中；username 唯一（重複 insert → `IntegrityError`）。
- `get_by_email` 已移除（不再存在）。
- **`normalize_username`**：`" Root "` → `"root"`；已小寫維持不變（純函式單測）。
- **`admin_role` 欄位**：未指定 → DB `server_default` 落為 `'viewer'`；寫入不在值域的字串（如 `'root'`）→ `IntegrityError`（`ck_admins_admin_role`；SQLite 需 CHECK 生效，conftest 已具備）。

### 8.2 Unit — AdminService（`tests/unit/services/test_admin_service.py`）
- `create(username, name, password)`：建 principal(role=1)+admin；username 正規化為小寫儲存；重複 username（含大小寫變體）→ `ConflictError`，且**不留孤兒 principal**。
- **`create` 格式驗證**：驗**正規化後**（strip+lower）的值——內部空白／`@`／`/` 等非法字元或過短（<3）不合 `_USERNAME_RE` → **`BadRequestError`**（400，service 層業務規則的域例外，**非** pydantic `ValidationError`；與 §5.3 一致），不建立任何列。⚠️ 純大小寫或前後空白會先被正規化（`Root`→`root`、` root `→`root`）故**不算**格式錯誤，勿誤列為無效案例。
- **`create` 的 admin_role 預設**：不傳 `admin_role` → 建出的 admin `admin_role == AdminRole.VIEWER`（fail-safe）；明確傳 `AdminRole.SUPER_ADMIN` → 如實儲存。
- `get`：預設對軟刪除者拋 `NotFoundError`；`include_deleted=True` 可取回。
- **`archive`**：設 `archived_at`、`is_active` 轉 False、該 admin 的 refresh token 全撤；再 `archive` idempotent。傳 `actor_principal_id` → `archived_by` 記錄之；不傳 → NULL。
- **`unarchive`**：清 `archived_at` **與 `archived_by`（成對）**、`is_active` 轉回 True；不自動發新 token。
- **`delete`（軟刪除）**：設 `deleted_at`、`is_active` 轉 False、refresh token 全撤；**principal 與 admin 列仍在 DB**（軟刪除，非物理刪除）；對已刪者再 `delete` → `NotFoundError`。傳 `actor_principal_id` → `deleted_by` 記錄之。
- **`restore`**：對軟刪除者清 `deleted_at` **與 `deleted_by`（成對）**、`is_active` 轉回 True、`get`（預設）可再取回；對未刪除者 idempotent；復原後**同 username 仍為該列**（未被佔用）。
- **稽核成對不變式**：任一狀態下 `archived_at IS NULL ⟺ archived_by IS NULL`、`deleted_at IS NULL ⟺ deleted_by IS NULL`（斷言四種轉移後皆維持）。
- 封存 / 軟刪除只影響該 principal 的 token，不波及其他 admin / user。

### 8.3 Unit — AuthService.admin_login（改 `test_auth_service` 相關）
- 正確 username+password（含大小寫變體，經 DTO/normalize）→ role=1 token + refresh。
- 錯密碼 / 不存在 / **已封存** / **已軟刪除** → `UnauthorizedError`（統一訊息），不發 token。
- **常數時間**：(a) `verify_password_or_dummy(None, "x")` → `False` 且**實際跑過一次 argon2**（純函式單測，放 `tests/unit/.../test_password.py`）；(b) `admin_login` 對不存在帳號**有呼叫** `verify_password_or_dummy`（mock/spy 斷言，不靠計時器）。
- `refresh`：封存 / 軟刪除的 admin 之既有 refresh token → 401（已撤 + `is_active` False 雙重擋）。

### 8.4 Integration（`tests/integration/test_admin_auth_api.py`）
- `POST /admin/auth/login` 以 username 正確 → `200`（含 refresh + expires_in）；大小寫變體（`Root`）亦成功（正規化）；錯密碼 → `401`。
- `GET /admin/me` → `200`，body 恰為 `{id, username, name, admin_role}`（**不含 `email`、不含狀態時間戳**）；seed admin 之 `admin_role == "super_admin"`。
- 封存 / 軟刪除後（經 service 佈局）該 admin 登入 → `401`；既發 refresh token 走 `/auth/refresh` → `401`。
- 既有 `test_admin_auth_api.py` 中 email 佈局全數改 username 後仍全綠。

> **既有測試遷移清單（email→username）**：
> - `tests/conftest.py`：常數 `ADMIN_EMAIL`（`= "admin@example.com"`）→ `ADMIN_USERNAME`（如 `"root"`）；`admin` fixture 的 `service.create(email=ADMIN_EMAIL, name="Root", ...)` → `create(username=ADMIN_USERNAME, name="Root", ..., admin_role=AdminRole.SUPER_ADMIN)`（測試用 root admin 給最高權限，貼近 seed 且不擋未來 rbac 授權測試）。需要 viewer/editor 的測試自行以 `admin_role=` 建立。
> - `tests/unit/repositories/test_admin.py`、`tests/unit/services/test_admin.py`、`tests/unit/services/test_admin_auth.py`、`tests/integration/test_admin_auth_api.py`：所有 `email=` 佈局與登入 payload 改 username；斷言 `AdminResponse` 不含 `email`、含 `username`。
> - `tests/payloads.py` **無 admin 條目**（admin 登入 payload 目前寫在測試內），不需改。

---

## 9. 實作順序（TDD 里程碑）

0. **`normalize_username`**（`app/core/security.py`）+ **`AdminRole` enum**（`app/core/enums.py`）+ 純函式／enum 單測（8.1）——先落地共用入口與等級 enum，後續各層都依賴。
1. **Model**：`Admin` 去 email/is_active 欄、加 username/**admin_role（預設 viewer + CHECK）**/archived_at/archived_by/deleted_at/deleted_by（後兩對含 `FK→principals SET NULL`）、加 `is_active` 計算屬性、`__repr__` 改 username；`models/__init__` 不變。先補 8.1 model 測試（含 admin_role 預設與 CHECK 值域）（RED→GREEN）。
2. **Migration**：**修訂原始 `c3d4e5f6a7b8`** 建表（§3.2，含 admin_role 欄 + `ck_admins_admin_role`，非新增 revision）；本機 dev DB 先 `downgrade b2c3d4e5f6a7` 再 `upgrade head`；真 MariaDB 驗 upgrade/downgrade base。
3. **Repository**：`get_by_email`→`get_by_username`（8.1）。
4. **AdminService**：注入 `RefreshTokenRepository`；`create` 改 username + `normalize_username` + `_USERNAME_RE` 驗證 + **`admin_role` 參數（預設 VIEWER）**、`get(include_deleted)` 過濾軟刪除、新增 `archive`/`unarchive`/`restore`、`delete` 改軟刪除 + 撤 token（8.2）。
5. **常數時間 primitive + admin_login**：先在 `password.py` 加 `_DUMMY_PASSWORD_HASH`（同步 hasher 算）+ `verify_password_or_dummy` helper（8.3a）；`admin_login` 改 username 查詢、去 mask_email、改用該 helper（8.3b）；確認 refresh 共用路徑不需改。
6. **DTO / Router**：`AdminLoginRequest`（含 `field_validator` 正規化）、`AdminResponse`（`id/username/name/admin_role`，**新增等級欄**）、`/admin/auth/login` 改吃新 DTO（8.4）。
7. **Config / Seed / .env.example**：email→username（+ name），`create_admin.py` 冪等改 `get_by_username` + **以 `SUPER_ADMIN` 建立**。
8. **測試佈局遷移**：conftest `admin` fixture + `ADMIN_USERNAME` 常數、4 個既有 admin 測試 email→username 全綠（§8 遷移清單）。
9. **提交前檢查**：`ruff check` / `ruff format --check` / `pyright` / `pytest` 全綠；真 MariaDB 驗修訂後 migration upgrade/downgrade。

---

## 10. 已定案決策

- ✅ **移除 `admins.email`**；新增 **`username`（String(100)、唯一、非加密、小寫正規化）** 作登入識別；`Admin` 不再依賴 `DeterministicEncryptedString` / `_ENCRYPTION_KEY`。
- ✅ 新增 **`archived_at` / `deleted_at`** 時間戳 **＋ `archived_by` / `deleted_by` 操作者稽核欄**（nullable `FK→principals.id`, `SET NULL`；成對不變式；service 帶可選 `actor_principal_id`，管理 API 上線後填實）——「何時＋誰」達 audit-grade。
- ✅ **`is_active` 由實體欄位改為計算屬性**（`archived_at`/`deleted_at` 皆 NULL 才 True），使登入／refresh／授權的既有 `not child.is_active` 判斷**零改動**即涵蓋封存與軟刪除。**User 的 `is_active` 實體欄位不動**。
- ✅ **`delete` 改為軟刪除**（設 `deleted_at`，不刪 principals、不 CASCADE）；新增 `archive`/`unarchive`/`restore`（狀態機對稱）；封存與軟刪除都**撤銷該 principal 的 refresh token**（access token 沿用 ≤30 分殘留窗口取捨）。
- ✅ Admin 登入改 **`AdminLoginRequest`（username + password，DTO validator 正規化）**，與 user 的 `LoginRequest`（email）區隔。
- ✅ **正規化單一入口 `normalize_username`**（`app/core/security.py`），DTO／service／seed 共用；**格式 `^[a-z0-9._-]{3,100}$` 只在建立路徑強制**（登入只正規化，格式不符 → 統一 401 不 422）。`create` 格式違規拋 **`BadRequestError`（400）**——service 層一律拋域例外，不手造 pydantic `ValidationError`（§5.3；§8.2 測試計畫原誤植為 `ValidationError`，已對齊修正）。
- ✅ 軟刪除者 **username 永久保留**（本專案不做 purge；部分索引不可攜、組合唯一對 NULL 不強制、改名損稽核 → 皆劣，見 §2.6）。
- ✅ **Migration 就地修訂原始 `c3d4e5f6a7b8`**（該表未部署 → 不製造 add-then-remove 髒歷史；「migration 不可變」只適用已部署 revision，見 §3.2）。
- ✅ **AdminResponse ＝ `id/username/name/admin_role`**（`/admin/me` 曝露自身等級供前端渲染；為權威來源）；狀態時間戳留給未來 `AdminSummary`（管理列表）。
- ✅ **admin_login 補常數時間 verify**：抽共用 `verify_password_or_dummy` + 同步算的 `_DUMMY_PASSWORD_HASH`（放 `password.py`），消除帳號列舉時序洩漏（admin 高價值目標）；user 端日後複用同一 helper。
- ✅ **提前交付 `AdminRole` enum + `admins.admin_role` 欄**（`String(20)` + `CHECK`，**預設 `VIEWER`** fail-safe、seed 給 `SUPER_ADMIN`、`create` 可帶等級）；與型別判別子 `role` 明確區分（兩欄用途註解）。**授權面（grade claim / 階梯授權 / set_role）仍屬 rbac.md**（見 §2.7、§11 項 5）。

## 11. 後續建議（本規格外，非阻塞）

> 以下皆已在本規格**明確劃為 out of scope 並定調處理方向**，非待決問題；列此供後續排程。

1. **Admin 管理 API**：`archive`/`unarchive`/`restore`/`delete` 的 CMS HTTP 端點 + 列表（`AdminSummary` 含狀態欄）+ 授權（限 super-admin，見 [`rbac.md`](./rbac.md)）。本規格已把這些能力備妥在 service 層。
2. ~~**Purge 與保留期**~~ **本專案不採**：已決定只做軟刪除（§2.6）；軟刪除的 username 永久保留、admin 列無 PII。**僅在**未來確有「同名重用」或法遵硬刪除需求時，才引入 `purge(admin_id)`（物理刪 principal → CASCADE），屆時另議。
3. **User 端登入時序側通道**：`AuthService.login` 沿用與舊 admin_login 相同的短路 verify（§7），建議以同樣的 dummy-verify 手法一併修正（本規格僅修 admin 端以控制 blast radius）。
4. **admin 登入 rate-limit / lockout**：延續 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md) §11 既有缺口，複用 Redis 對 `/admin/auth/login` 計數限流。
5. **✅ 已對齊 [`rbac.md`](./rbac.md)（連同 jwt-role 兩份規格與兩份決策）**：交付 `admin_role` 後已同步更新——rbac.md 移除重複的 admin enum/建欄/seed（改標「已由本規格交付」）、migration 只留 `users.user_tier`、`AdminResponse` 更正為 `id/username/name/admin_role`、seed 改 `create(..., SUPER_ADMIN)` 而非 `UPDATE WHERE email`；[`jwt-role-and-admin.md`](./jwt-role-and-admin.md) 加「admin email/is_active 已被本規格取代」註記；`decisions/rbac.md`、`decisions/jwt-role-and-admin.md` 加交付位置／演進註記。rbac.md 續由其負責 **grade claim + `require_min_admin_role` + `set_role` + `user_tier`**。
