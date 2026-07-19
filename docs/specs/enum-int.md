# 計畫：`AdminRole` / `UserTier` 改為 `IntEnum`（int 一路到底）

> 狀態：**定案（待實作）** ／ 開發模式：**嚴格 TDD**
>
> 📎 目的：把 `AdminRole`/`UserTier` 從 `StrEnum` 改為 `IntEnum`，讓**排序即值（rank = value）**，支援 **SQL 層直接比 rank**（`WHERE admin_role >= 50`、`ORDER BY`），並**移除分離的 `ADMIN_ROLE_RANK`/`USER_TIER_RANK` dict**。

## 動機（誠實記錄，避免後人誤解）
- ✅ **SQL 層比 rank**：可在 DB 直接 `WHERE admin_role >= :min`、`ORDER BY admin_role`（字串做不到、或得 CASE、且無法吃索引）。這是 IntEnum 唯一無法被 StrEnum 取代的優勢，也是本次動機。
- ✅ **排序單一真相**：rank 即 enum 值 → 刪掉 `ADMIN_ROLE_RANK`/`USER_TIER_RANK` dict，`require_min_*` 直接比值、無 dict 漂移。
- ⚠️ **不是為了「隱藏等級 / obscurity」**：JWT 為 base64 明文可解、`/me` 仍回等級、授權查 DB 不信 claim → int 藏不住也不需藏。動機純為 SQL rank + 單一排序真相。

## 值指派（大間隙，供未來插值免重編號）
```python
class AdminRole(IntEnum):
    VIEWER = 0
    EDITOR = 50
    SUPER_ADMIN = 100
    ROOT = 999          # bootstrap root（is_protected）；階梯最高「天花板」，供 root-only API gating

class UserTier(IntEnum):
    FREE = 0
    PREMIUM = 5
```
- **AdminRole 大間隙（0/50/100/999）**：未來要在既有級之間插新級，取中點即可（如 editor(50) 與 super_admin(100) 間插 75），既有列不重編號、不汙染。`UserTier` 目前僅兩級、間隙 5（0/5）已足。
- **`ROOT=999`（現在就立，見 `bootstrap-hidden-admin.md` §2.6）**：root 的 grade 為 `ROOT`（非 SUPER_ADMIN）。`ROOT > SUPER_ADMIN` → root 通過所有 super_admin 檢查（一般 API 全可用）；未來「root-only API」以 `require_min_admin_role(AdminRole.ROOT)` gate。
  - **為何用 999（大跳、非間隙 5）**：ROOT 是階梯**天花板**，刻意與 SUPER_ADMIN(100) 拉開一大段，**保留 101..998 供未來在 super_admin 與 root 之間插入中階 grade**（如 `org_admin`），永不撞頂。SmallInteger（−32768..32767）容得下。
  - **📌 不變式：`admin_role` 最大值 < 1000**（ROOT=999 為硬上限，`≥1000` 保留、不得使用）。所有 admin_role 值恆落在 `[0, 999]`；新增 grade 一律 < 1000。此上限應於 model CHECK 反映（`admin_role IN (0,50,100,999)` 已隱含；亦可加 `admin_role < 1000` 作明確兜底）。見 `admin-management-model.md`。
  - IntEnum + 這個設計讓「加最高階 grade」是一行、免 migration 動既有列——此即採 IntEnum 的紅利。
- `Role(IntEnum)`（USER=0/ADMIN=1）本就是 IntEnum，風格更一致。

## 觸點與改法

| 層 | 檔案:行 | 改動 |
|---|---|---|
| enums | `app/core/enums.py:4,16` | `AdminRole`/`UserTier` → `IntEnum`（上列值）；**刪 `ADMIN_ROLE_RANK`/`USER_TIER_RANK`（第 28-37 行）** |
| 授權（直接比值） | `app/api/dependencies/auth.py:103,117`、`app/api/routers/ws/router.py:141`、`app/services/admin.py:222` | `ADMIN_ROLE_RANK[AdminRole(x)] < ADMIN_ROLE_RANK[min]` → `x < min`（DB 讀出是 int，`minimum` 是 IntEnum，`int < IntEnum` 直接可比）；移除 `ADMIN_ROLE_RANK`/`USER_TIER_RANK` import |
| model | `app/models/admin.py:45-48,80-85` | `admin_role: Mapped[int] = mapped_column(SmallInteger, default=AdminRole.VIEWER.value, server_default=text("0"))`；CHECK `admin_role IN (0,50,100,999)`；**`is_protected = 0 OR admin_role = 100`**（純翻譯 `= 'super_admin'`→`=100`；**protected→ROOT(999) 屬 Phase 2 bootstrap**，非本次；§server_default） |
| model | `app/models/user.py:48-51,66` | `user_tier: Mapped[int] = mapped_column(SmallInteger, default=UserTier.FREE.value, server_default=text("0"))`；CHECK `user_tier IN (0,5)` |
| **WS Connection** | `app/services/ws/manager.py:41,50` | `admin_role: str` → `int`（`__init__` 參數 + `self.admin_role`）——存等級供 topic rank 比較 |
| **WS DTO（wire）** | `app/dtos/ws.py:20` | `WelcomeMessage.admin_role: str` → `int`（方案 A：WS welcome 也回 int）。ws/router.py:255,264 餵入 `admin.admin_role`（現 int），連得起來 |
| **JWT grade（3 簽章）** | `app/core/auth/jwt.py:22,77`、`app/services/auth.py:67` | `create_access_token(grade: str\|None)`→`int\|None`；`extract_grade(...)->str\|None`→`int\|None`；`_grade_of(child)->str`→`int`（回 `admin.admin_role`/`user.user_tier`，現 int）。⚠️ grade 在 app/ **只產出、不被當字串消費**（已驗）→ runtime 安全，但不改簽章 pyright 會擋 |
| DTO/schema | `app/api/routers/admin/schemas.py:33,43,56,73,92`、`app/api/routers/users/schemas.py:24` | `admin_role: AdminRole`/`tier: UserTier`（IntEnum）→ Pydantic v2 **預設序列化成 int**（§Pydantic）；`AdminRole(a.admin_role)`（int→member）不變 |
| migration | `alembic/versions/<new>.py` | 資料轉換 + 欄型 `VARCHAR`→`SMALLINT` + 換 CHECK（§migration） |
| 測試 | 見 §測試熱點（10 檔 behavior-change） | 字串斷言（`"super_admin"`/`"free"`/grade/tier）→ int |

> **對外契約一起變 int（方案 A，已定案）**：`POST/PATCH/GET /admin/admins` 的 `admin_role`、`GET /users/me` 的 `tier`、JWT `grade`、WS `welcome.admin_role` 全變 int。前端配合調整（已同意）。

> **文件更新（doc-drift）**：`enums.py` 的 docstring、`docs/specs/rbac.md`（grade claim 描述）、`docs/specs/jwt-role-and-admin.md`、`docs/specs/websocket.md`（welcome `admin_role`）現皆述為「字串等級」→ 一併改為 int（rank=value）。屬收尾，不阻塞開發。

### 接線細節（補完，開發前必讀）

**server_default 形式**：`server_default` **不能給裸 int**，須 `text("0")`（`from sqlalchemy import text`）；欄型 `SmallInteger`（`from sqlalchemy import SmallInteger`）。Python 側 `default=AdminRole.VIEWER.value`（int 0）。

**Pydantic 序列化**：Pydantic v2 對 `IntEnum` 欄位**預設輸出 int、輸入接受 int**（無需 `use_enum_values` 或自訂 serializer）。故 `AdminSummary.admin_role`/`AdminCreateRequest.admin_role`/`AdminRoleUpdateRequest.admin_role`/`UserResponse.tier` 自動 int in/out。

**grade 消費端（已查證）**：`extract_grade` 僅於 `app/core/auth/__init__.py` 匯出、**app/ 內無邏輯把 grade 當字串比對**（只在 `auth.py` 產出塞進 token）→ 改 int 無 runtime 破壞，僅需 3 處簽章改型別。

**⚠️ 不用改的站點（避免過度修改）**：
- **純成員引用**：`require_min_admin_role(AdminRole.SUPER_ADMIN)`（`admin/router.py:39`、`monitoring/router.py:37,39`）、`TOPIC_MIN_ROLE` dict（`ws/topics.py:11-13`）、`admin_role is not AdminRole.SUPER_ADMIN`（`admin.py:217`，identity 比較）——**全不動**（IntEnum 成員仍在）。
- **`.value` 站點**（自動變 int、且欄位收 int，OK 不動）：`admin.py:140`（`admin_role=admin_role.value`）、`initial_admin.py:55`、`auth.py:218`（`grade=AdminRole.SUPER_ADMIN.value` 恰為 int grade）、`admin.py:245`（`admin.admin_role == AdminRole.SUPER_ADMIN.value`，int==int）。

## Migration（MariaDB；SQLite 測試走 create_all 不受影響）
`upgrade()`（admins.admin_role，users.user_tier 同法）：
1. `DROP` 依賴 CHECK（`ck_admins_admin_role`、`ck_admins_protected_is_super`、`ck_users_user_tier`）。
2. `UPDATE admins SET admin_role = CASE admin_role WHEN 'viewer' THEN '0' WHEN 'editor' THEN '50' WHEN 'super_admin' THEN '100' END`（users：`'free'→'0'`、`'premium'→'5'`）。
3. `ALTER TABLE admins MODIFY admin_role SMALLINT NOT NULL DEFAULT 0`（MariaDB 自動 cast 數字字串）；users 同。
4. 重建 CHECK：`admin_role IN (0,50,100,999)`、**`is_protected = 0 OR admin_role = 100`**（純翻譯，protected 仍 super_admin=100）、`user_tier IN (0,5)`。
   > ⚠️ **Phase 分工（實作定案）**：本 migration 是**純表示轉換、零語意變更**——`is_protected⟹super_admin` 只翻成 `⟹100`（不是 999）。**protected→ROOT(999) 的轉移 + 守衛改 ROOT + seed root 屬 Phase 2 bootstrap**，由 bootstrap 的一支小 migration 把此 CHECK 從 `⟹100` 改為 `⟹999`。如此 Phase 1 保持既有 protected-super_admin 測試全過（100=100）、不提前引入 ROOT 語意。

`downgrade()`：反向（int→字串、欄型還原 VARCHAR、CHECK 還原）。

> 動手前 `alembic heads` 確認 `down_revision`（撰稿時 head `e5f6a7b8c9d0`）。

## TDD 里程碑（先寫、先看 RED）
1. **enum 語意**（`tests/unit/test_enums.py`）：`AdminRole.VIEWER==0/EDITOR==50/SUPER_ADMIN==100/ROOT==999`、`ROOT>SUPER_ADMIN>EDITOR>VIEWER`、`UserTier.FREE==0/PREMIUM==5`。→ 改 enums.py（+刪 dict）。
2. **SQL rank 能力**（core 動機）：種不同 role 的 admin，`SELECT ... WHERE admin_role >= 50` 回 editor+super_admin、`ORDER BY admin_role` 依權限序。→ 證明本次目的達成。
3. **授權直接比值**：`require_min_admin_role`/`require_min_tier` 以 int 比較放行/403（改 3 處、刪 dict、移 import）。
4. **model/CHECK**：`admin_role` 非法值（如 3）→ IntegrityError；`is_protected=True` 但 `admin_role != 100`（例如 editor=50）→ IntegrityError（Phase 1：protected ⟹ super_admin=100）。
5. **JWT grade / WS welcome 為 int**：登入 token 的 `grade` claim 為 int；WS welcome 的 `admin_role` 為 int（3 簽章改型別 + WS DTO/manager 改 int）。
6. **migration**：MariaDB upgrade/downgrade 資料轉換正確（真 DB 驗）。
7. 更新既有 behavior-change 測試（§測試熱點）字串→int；全套綠 + 提交前檢查（ruff/format/pyright/pytest）。

## 測試熱點（behavior-change 的 10 檔；斷言字串→int）
| 檔 | 熱點 |
|---|---|
| `tests/unit/test_enums.py` | enum 值/rank（已由里程碑 1 重寫，含 SQL rank 測試） |
| `tests/unit/test_admin_model.py` | `admin_role` 欄位值、CHECK |
| `tests/unit/test_jwt.py` | `grade` claim 產出/`extract_grade` 型別 |
| `tests/unit/test_authz_deps.py` | `require_min_admin_role`/`require_min_tier` 比較 |
| `tests/unit/services/test_rbac_grade.py` | grade 刷新（int） |
| `tests/integration/test_user_api.py` | `/users/me` 回 `tier`（int） |
| `tests/integration/test_admin_management_api.py` | `admin_role` 送/收（int）、列表 |
| `tests/integration/test_ws_handshake_api.py` | WS welcome `admin_role`（int） |
| `tests/integration/test_initial_admin.py` | 初始 admin grade/role（int） |
| `tests/integration/test_admin_auth_api.py` | 登入 token grade（int）、改 role |

> 其餘測試（不碰 role/tier/grade 字面）不受影響。純成員引用（`AdminRole.EDITOR`）在測試裡也不用改。

## 與 `bootstrap-hidden-admin.md` 的交互
- `auth.py:218` 的 `grade=AdminRole.SUPER_ADMIN.value` 位於初始 admin 登入分支——該分支於 bootstrap 重構會移除。**兩者獨立**：本 enum 重構先做亦可（該行值只是 `"super_admin"`→`100`）；bootstrap 重構後那行連同分支消失。順序不拘，先做 enum-int 較單純。
