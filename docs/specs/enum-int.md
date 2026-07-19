# 計畫：`AdminRole` / `UserTier` 改為 `IntEnum`（int 一路到底）

> 狀態：**定案（待實作）** ／ 開發模式：**嚴格 TDD**
>
> 📎 目的：把 `AdminRole`/`UserTier` 從 `StrEnum` 改為 `IntEnum`，讓**排序即值（rank = value）**，支援 **SQL 層直接比 rank**（`WHERE admin_role >= 5`、`ORDER BY`），並**移除分離的 `ADMIN_ROLE_RANK`/`USER_TIER_RANK` dict**。

## 動機（誠實記錄，避免後人誤解）
- ✅ **SQL 層比 rank**：可在 DB 直接 `WHERE admin_role >= :min`、`ORDER BY admin_role`（字串做不到、或得 CASE、且無法吃索引）。這是 IntEnum 唯一無法被 StrEnum 取代的優勢，也是本次動機。
- ✅ **排序單一真相**：rank 即 enum 值 → 刪掉 `ADMIN_ROLE_RANK`/`USER_TIER_RANK` dict，`require_min_*` 直接比值、無 dict 漂移。
- ⚠️ **不是為了「隱藏等級 / obscurity」**：JWT 為 base64 明文可解、`/me` 仍回等級、授權查 DB 不信 claim → int 藏不住也不需藏。動機純為 SQL rank + 單一排序真相。

## 值指派（間隙 5，供未來插值免重編號）
```python
class AdminRole(IntEnum):
    VIEWER = 0
    EDITOR = 5
    SUPER_ADMIN = 10

class UserTier(IntEnum):
    FREE = 0
    PREMIUM = 5
```
- 間隙 5：未來要插級（如 editor 與 super_admin 間）用 2/7，既有列不重編號、不汙染。
- `Role(IntEnum)`（USER=0/ADMIN=1）本就是 IntEnum，風格更一致。

## 觸點與改法

| 層 | 檔案 | 改動 |
|---|---|---|
| enums | `app/core/enums.py` | `AdminRole`/`UserTier` → `IntEnum`（上列值）；**刪 `ADMIN_ROLE_RANK`/`USER_TIER_RANK`** |
| 授權（直接比值） | `app/api/dependencies/auth.py:103,117`、`app/api/routers/ws/router.py:141`、`app/services/admin.py:222` | `ADMIN_ROLE_RANK[AdminRole(x)] < ADMIN_ROLE_RANK[min]` → `x < min`（int 比 IntEnum，直接可比） |
| model | `app/models/admin.py` | `admin_role: Mapped[int] = mapped_column(SmallInteger, default=AdminRole.VIEWER, server_default=text("0"))`；CHECK `admin_role IN (0,5,10)`；`is_protected = 0 OR admin_role = 10` |
| model | `app/models/user.py` | `user_tier: Mapped[int] = SmallInteger, default=UserTier.FREE, server_default=text("0")`；CHECK `user_tier IN (0,5)` |
| JWT grade | `app/services/auth.py` | `grade=_grade_of(...)`／`grade=AdminRole.SUPER_ADMIN.value` 值變 int → JWT `grade` claim 為 int（消費端／前端調整） |
| DTO/schema | `app/api/routers/admin/schemas.py`、`app/api/routers/users/schemas.py` | `admin_role: AdminRole`/`tier: UserTier`（IntEnum）→ 序列化為 int：API 送/收 role/tier 為 int |
| migration | `alembic/versions/<new>.py` | 資料轉換 + 欄型 `VARCHAR`→`SMALLINT` + 換 CHECK（§migration） |
| 測試 | `tests/**`（~21 檔） | 字串斷言（`"super_admin"`/`"free"`）→ int/enum |

> **對外契約一起變 int（方案 A）**：`POST/PATCH/GET /admin/admins` 的 `admin_role`、`GET /users/me` 的 `tier`、JWT `grade` 全變 int。前端配合調整（已同意）。

## Migration（MariaDB；SQLite 測試走 create_all 不受影響）
`upgrade()`（admins.admin_role，users.user_tier 同法）：
1. `DROP` 依賴 CHECK（`ck_admins_admin_role`、`ck_admins_protected_is_super`、`ck_users_user_tier`）。
2. `UPDATE admins SET admin_role = CASE admin_role WHEN 'viewer' THEN '0' WHEN 'editor' THEN '5' WHEN 'super_admin' THEN '10' END`（users：`'free'→'0'`、`'premium'→'5'`）。
3. `ALTER TABLE admins MODIFY admin_role SMALLINT NOT NULL DEFAULT 0`（MariaDB 自動 cast 數字字串）；users 同。
4. 重建 CHECK：`admin_role IN (0,5,10)`、`is_protected = 0 OR admin_role = 10`、`user_tier IN (0,5)`。

`downgrade()`：反向（int→字串、欄型還原 VARCHAR、CHECK 還原）。

> 動手前 `alembic heads` 確認 `down_revision`（撰稿時 head `e5f6a7b8c9d0`）。

## TDD 里程碑（先寫、先看 RED）
1. **enum 語意**（`tests/unit/test_enums.py`）：`AdminRole.VIEWER==0/EDITOR==5/SUPER_ADMIN==10`、`EDITOR > VIEWER`、`UserTier.FREE==0/PREMIUM==5`。→ 改 enums.py（+刪 dict）。
2. **SQL rank 能力**（core 動機）：種不同 role 的 admin，`SELECT ... WHERE admin_role >= 5` 回 editor+super_admin、`ORDER BY admin_role` 依權限序。→ 證明本次目的達成。
3. **授權直接比值**：`require_min_admin_role`/`require_min_tier` 以 int 比較放行/403（改 3 處、刪 dict）。
4. **model/CHECK**：`admin_role` 非法值（如 3）→ IntegrityError；`is_protected=True` 但 `admin_role!=10` → IntegrityError。
5. **migration**：MariaDB upgrade/downgrade 資料轉換正確（真 DB 驗）。
6. 更新既有 ~21 測試檔字串→int；全套綠 + 提交前檢查（ruff/format/pyright/pytest）。

## 與 `bootstrap-hidden-admin.md` 的交互
- `auth.py:218` 的 `grade=AdminRole.SUPER_ADMIN.value` 位於初始 admin 登入分支——該分支於 bootstrap 重構會移除。**兩者獨立**：本 enum 重構先做亦可（該行值只是 `"super_admin"`→`10`）；bootstrap 重構後那行連同分支消失。順序不拘，先做 enum-int 較單純。
