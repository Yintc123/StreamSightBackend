# 實作 Roadmap：三大重構的順序、硬依賴與共同紀律

> 狀態：**規劃（實作總圖）** ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> 📎 本文是「近期三大規劃」的**單一實作入口**——把散在各規格的順序與依賴收斂成一張總圖，避免踩到跨重構的致命依賴（尤其 §依賴圖 的紅線）。各步的細節仍以其規格為權威：
> - [`enum-int.md`](./enum-int.md)（AdminRole/UserTier → IntEnum + ROOT）
> - [`bootstrap-hidden-admin.md`](./bootstrap-hidden-admin.md)（初始 admin 落地為真實 DB root）
> - [`records-model.md`](./records-model.md) / [`records-service.md`](./records-service.md) / [`records-api.md`](./records-api.md)（資料記錄 feature）
> - admin-management ×3 的 delta（受 enum-int/bootstrap 影響，實作時一併落地）

---

## 0. 依賴圖（先看這裡）

```
        ┌───────────────┐
        │  enum-int     │  IntEnum(0/50/100/999)+ROOT、grade int、CHECK is_protected⟹999、migration
        └──────┬────────┘
   🔴 HARD 依賴 │  （bootstrap seed root=ROOT(999)+protected 需要：①ROOT 成員存在 ②CHECK 已改 ⟹999）
        ┌──────▼────────┐
        │  bootstrap    │  ensure_initial_admin(startup upsert) + §2.6 root 保護守衛 + 移除哨兵 + 登入一般化
        └───────────────┘

        ┌───────────────┐
        │  records      │  獨立：用 require_min_admin_role（enum 值透明）、真實 admin 由既有 `admin` fixture 提供
        └───────────────┘   → 無硬依賴，可與上鏈並行；建議排最後（在穩定底座上做最單純）
```

- **🔴 唯一致命依賴：`enum-int` 必須先於 `bootstrap`。** 若順序反了——在 enum-int 前就 seed `admin_role=999, is_protected=True`——會撞舊 CHECK `is_protected = 0 OR admin_role = 'super_admin'`（999≠'super_admin'）→ `IntegrityError` → **app 開不了機**。反之，enum-int 先落地後 CHECK 已是 `⟹999`，seed 才成立。
- **`records` 與其他兩者無硬依賴**：records 只用 `require_min_admin_role(EDITOR/VIEWER)`（enum 值改不改它都透明）、`created_by` FK 指向任何真實 admin（測試由 `admin` fixture 供）。**前提**：records 不得再加「擋初始 admin」的哨兵守衛（規格已移除）。
- **admin-guard（§2.6 `_guard_protected_target`）隨 bootstrap 落地**（它依賴 is_protected 真的被用 + ROOT gating）。

## 建議順序：**enum-int → bootstrap（+guard）→ records**
理由：先穩固 auth/model 底座（enum-int、bootstrap 都動核心授權/帳號），再疊 feature（records）。records 雖可先做，放最後在已穩定的基座上最單純、回歸面最小。

---

## 1. Phase 1 — `enum-int`（底座）

- **前置**：`uv run alembic heads` 確認 `down_revision`。
- **交付**（權威見 `enum-int.md`）：`AdminRole`/`UserTier` → IntEnum（含 `ROOT=999`）；刪 `ADMIN_ROLE_RANK`/`USER_TIER_RANK`（直接比值）；grade claim + WS welcome + API 對外 int（3 簽章、WS manager/DTO、Pydantic 自動）；migration（`String→SmallInteger`、`admin_role IN (0,50,100,999)`、`is_protected⟹999`、`user_tier IN (0,5)`、資料 CASE 轉換）。
- **TDD**：`enum-int.md` §TDD 里程碑 1–7（enum 語意 → SQL rank → 授權直接比值 → CHECK → grade/WS int → migration → 更新 10 檔 behavior-change）。
- **完成判準**：`uv run alembic upgrade head` 成功；提交前檢查全綠（ruff/format/pyright/pytest）。
- **BLOCKS**：Phase 2（bootstrap）。

## 2. Phase 2 — `bootstrap-hidden-admin`（+ root 保護守衛）

- **前置（🔴）**：**Phase 1 已 `upgrade head`**（DB 的 CHECK 已是 `is_protected⟹999`、`admin_role` 為 int、`AdminRole.ROOT` 存在）。否則 seed 撞 CHECK → 開不了機（§0）。
- **交付**（權威見 `bootstrap-hidden-admin.md`）：`ensure_initial_admin`（startup upsert、`_validate_admin_fields` 政策驗證、三 env 必填 fail-fast、`protected_root_exists()` 冪等鍵）；config 改 `initial_admin_password`（env 明文 SecretStr）；移除哨兵機制（initial_admin/auth/ws/reauth/admin 特判）；登入一般化 + `change_password` 開放；**§2.6 `_guard_protected_target`**（其他 admin 改 root → 403）套到 `update`/`set_admin_role`；共用密碼/username 政策驗證抽取。
- **一支小 migration**（protected CHECK `⟹100`→`⟹999` + 守衛 `SUPER_ADMIN`→`ROOT`，bootstrap §2.1）；root 身分本身走 startup upsert（非 migration）。enum-int（Phase 1）已把 CHECK 純翻譯為 `⟹100`，本 Phase 才做 protected→ROOT 語意轉移。
- **TDD**：`bootstrap-hidden-admin.md` §5（ensure_initial_admin 冪等/單一 root、config 必需+政策 fail-fast、C2 迴歸、登入、root grade=ROOT、§2.6 保護矩陣、lifespan 接線）。
- **完成判準**：提交前檢查全綠；真 MariaDB 驗 app 啟動 seed。

## 3. Phase 3 — `records`（feature）

- **前置**：無硬依賴（可與 Phase 1/2 並行）。migration `down_revision` 接於當下 head（若 Phase 1 已做，接其後）。
- **交付**（權威見 records ×3 + 各 §實作接線清單）：models（`Record`/`RecordCategory` + **`app/models/__init__.py` 註冊**）、repositories（含 `RecordListRow`/`get_active_row`）、`app/dtos/record.py`、`app/core/exceptions/record.py`、enums（`RecordSortField`/`SortDirection`）、config 常數、`get_record_service` DI、router `/records*`、migration（record_categories 四種子 + records）、conftest `record_categories` fixture。
- **TDD**：model §7 → service §6 → api §8（由內而外）。
- **完成判準**：提交前檢查全綠。

---

## 4. 測試 fixture 衝突解法（缺口 2 定案）

**問題**：`tests/conftest.py:42` `ADMIN_USERNAME="root"`；`admin` fixture 建一個 **super_admin、`is_protected=False`、username="root"** 的帳號。bootstrap 的真實 root 也叫 "root"（env）、是 **ROOT + protected**。兩者 username 相同 → 若同一測試併用會撞 `admins.username` UNIQUE，且語意混淆。

**定案**：
- **conftest `admin` fixture 維持不變**（username="root"、super_admin、`is_protected=False`）——它代表「**一般 super_admin**」，**非** bootstrap root。多數測試（lifespan 不跑）只有這個「root」，無衝突。
- **初始 admin 專屬測試（bootstrap §5）用不同的 env username**（如 `INITIAL_ADMIN_USERNAME="bootstrapadmin"`），且**不與 `admin` fixture 併用**——避免 UNIQUE 撞 + 概念混淆。opt-in seed fixture 以 `monkeypatch` 設這組 env 後呼叫 `ensure_initial_admin`。
- 此約定寫入 `bootstrap-hidden-admin.md` §5.2。

## 5. 跨 Phase 共同紀律（最佳實踐）

- **每 Phase 一個分支 / PR**（三者皆大、獨立可審）。Phase 1 合併並 `upgrade head` 後才動 Phase 2。
- **嚴格 TDD**：每行為先寫失敗測試、親眼看 RED、最小實作轉 GREEN、綠燈下重構（CLAUDE.md 黃金守則）。
- **每 Phase 結束跑提交前檢查**：`ruff check` / `ruff format --check` / `pyright` / `pytest` 全綠（對齊 CI）。
- **migration**：每次動手前 `alembic heads` 確認；append-only；真 MariaDB 驗 upgrade/downgrade。
- **新 model 註冊**：`app/models/__init__.py`（否則 `create_all` 測試抓不到表、autogenerate 失明）。
- **delta 落地**：各既有規格的「🔺 變更註記」為**權威來源**，實作時把對應內文逐段改寫（本 roadmap 不重述細節）。
- **文件收尾（不阻塞）**：`rbac.md`/`jwt-role-and-admin.md`/`websocket.md`/`enums.py` docstring 等「grade 字串」敘述於實作時一併改 int。

## 6. 依賴速查表

| 要做 | 硬前置 | 可並行 | 產出 migration |
|---|---|---|---|
| enum-int | 當下 head | records | ✅（String→int、CHECK `protected⟹100`） |
| bootstrap | **enum-int 已 upgrade** | records | ✅（小：`protected⟹100`→`⟹999`）＋ startup upsert |
| records | 無 | enum-int / bootstrap | ✅（record_categories + records） |
| admin §2.6 guard | 隨 bootstrap | — | ❌ |
