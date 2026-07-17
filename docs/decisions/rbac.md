# 設計決策：型別內權限等級（方案 A）

本文記錄「型別內權限等級」模組的**關鍵設計決策與取捨**（為什麼）。實作細節見規格書 [`../specs/rbac.md`](../specs/rbac.md)。相關既有決策：[`jwt-role-and-admin.md`](./jwt-role-and-admin.md)。

相關程式碼（實作後）：`app/core/enums.py`、`app/core/auth/jwt.py`、`app/models/user.py`、`app/models/admin.py`、`app/api/dependencies/auth.py`。

## 目錄

- [R1：採方案 A（等級 enum），不採方案 B（permissions 表）](#r1採方案-a等級-enum不採方案-bpermissions-表)
- [R2：權限層與型別判別子分離](#r2權限層與型別判別子分離)
- [R3：grade claim 用 StrEnum 字串，命名避開 role](#r3grade-claim-用-strenum-字串命名避開-role)
- [R4：AdminRole 為有序階梯 + require_min](#r4adminrole-為有序階梯--require_min)
- [R5：claim 僅 UX 提示，授權以 child 現值為準](#r5claim-僅-ux-提示授權以-child-現值為準)
- [R6：fail-safe 預設最低權限](#r6fail-safe-預設最低權限)

---

## R1：採方案 A（等級 enum），不採方案 B（permissions 表）

**決策**：每種身分用一個**等級 `StrEnum`**（`AdminRole` / `UserTier`）+ child 欄位 + `CHECK`，以有序階梯授權；**不**引入 `roles` / `permissions` / `role_permissions` 關聯表。

**脈絡**：需求是「admin 分三級、user 分級」——**數量少、當前是清楚的階梯**。方案 B（permissions M:N）能做細粒度可組合權限，但要多 3–4 張表、更多 join、以及權限可變帶來的 token 陳舊治理。

**理由**：對「幾個固定等級」而言，方案 B 是過度設計（對齊本專案不過度設計的風格，見 jwt-role D5）。等級 enum + DB CHECK 就能 schema 化業務規則、免額外表、授權是一次本地欄位讀取。

**取捨**：等級是「整包階梯」，無法表達「A 能做甲不能做乙、B 反之」的互不包含權限。**升級訊號**：當出現「同級內需再切細能力」或「跨級的能力非嚴格包含」時，就是改走方案 B 的時機（見 R4、規格 §11 Q2）。屆時 `grade` claim 可平滑換成 `perms` 集合。

---

## R2：權限層與型別判別子分離

**決策**：不重用 `principals.role`（0/1）承載權限；權限等級**另存各 child 欄**（`users.user_tier` / `admins.admin_role`）。

**脈絡**：曾可「把 admin 三級塞進 `principals.role`（role=1/2/3…）」省一欄。

**理由**：`principals.role` 是**型別判別子**——不可變、被 `CHECK(role IN (0,1))` + 複合 FK 釘死、決定走哪張 child 表（見 jwt-role D9）。權限等級是**可變**狀態。把兩者混在同一欄會：(a) 破壞複合 FK 的「型別-角色一致性」硬化；(b) 讓不可變的型別與可變的權限生命週期糾纏；(c) 撞號（role=2 到底是「型別 2」還是「admin 的第 2 級」？）。三軸分離（型別 / 權限 / 狀態）是本模組的地基。

**取捨**：多兩個 child 欄（各身分一欄）。換得型別判別子維持乾淨不可變、權限可獨立演進。

---

## R3：grade claim 用 StrEnum 字串，命名避開 role

**決策**：JWT 新增單一 `grade` claim，值為等級 `StrEnum` 的**字串**（`"editor"`）；claim key 用中性的 `grade`，**不叫 `role`**。enum 命名用 `SUPER_ADMIN / EDITOR / VIEWER`，避開 `USER` / `ADMIN`。

**理由**：
1. **字串而非整數**：`grade` 是給**前端讀來渲染 UI** 的，`"editor"` 自我描述、前端免硬編 magic number；對齊既有 `AppEnv`/`LogLevel` 皆 `StrEnum`。（`Role` 用 `IntEnum` 是因它是判別子、非給人讀。）
2. **單一 claim 足夠**：一張 token 非 user 即 admin，`grade` 值域由 `role` 決定；user/admin 又是分離前端，各情境語意單一，不需拆兩個 claim（拆了每次也只有一個有值）。
3. **命名避碰**：`Role.USER` / `Role.ADMIN` 已是型別；若 admin 等級再叫 `AdminRole.USER`、`AdminRole.ADMIN`，程式中 `AdminRole.USER` vs `Role.USER` 極易誤用，JWT 裡 `role=1` 配 `grade="user"` 也反直覺。故頂級叫 `SUPER_ADMIN`、中級 `EDITOR`。

**取捨**：`grade` 的解讀需搭配 `role`（同一 claim key、不同值域）——但因兩前端分離，實務上無歧義。

---

## R4：AdminRole 為有序階梯 + require_min

**決策**：`AdminRole` 是**權限高→低的階梯**（`SUPER_ADMIN > EDITOR > VIEWER`），以 `ADMIN_ROLE_RANK` 表達順序；授權用 `require_min_admin_role(minimum)` 比較 rank。

**理由**：三級是清楚的包含關係（高級涵蓋低級的能力）。用「最低門檻」表達端點需求（讀取免限、寫入要 EDITOR、管理 admin 要 SUPER_ADMIN）比「逐一列舉允許角色」精簡且不易漏。rank 表讓比較是 O(1) 本地運算。

**取捨**：階梯假設「高級 ⊇ 低級」。一旦不成立（某級有獨立能力、非嚴格包含），rank 比較就失真——那是升級到方案 B（permissions 集合）的明確訊號，不要硬凹階梯（見 R1、規格 §11 Q2）。

---

## R5：claim 僅 UX 提示，授權以 child 現值為準

**決策**：`grade` claim **只供前端渲染 UI**，**不是授權邊界**；後端授權 dependency 一律讀 **child 欄現值**（`admin.admin_role` / `user.user_tier`）判定。前端 UI 的**真實來源是 `/me`**（每次讀 DB、新鮮），claim 僅當「零往返初始提示」。

**脈絡**：權限等級是**可變**的（升降權、付費升級）。任何放進無狀態 JWT 的可變資料都會陳舊：改了等級，舊 access token 的 claim 仍是舊值，直到 token 汰換（≤ access TTL）。這比 jwt-role §7 記錄的「stateless 登出窗口」更需重視，因為是**授權變更**。

**理由**：把「授權判定」與「UI 提示」分開，各取所需：
- **授權讀 child** → 永遠新鮮，撤/降權對後端存取控制**即時生效**，竄改 claim 也無用。
- **UI 讀 `/me`** → 新鮮，不必解 JWT。
- **claim 作可選提示** → 首屏零往返，接受短暫過時。
- **refresh 自然刷新**：`refresh` 本就載入 child 驗 `is_active`（jwt-role §5.4），順手重讀最新等級重簽 `grade`，把陳舊窗口壓到 ≤ 一個 access TTL；需即時者前端強制 refresh 一次。

**取捨**：授權每次多讀 child——但 `get_current_admin`/`get_current_user` 本就已載入 child（含等級為本地欄位），**不增額外查詢**（受惠於 jwt-role D4b「識別/狀態屬性留 child」）。

---

## R6：fail-safe 預設最低權限

**決策**：`user_tier` 預設 `FREE`、`admin_role` 預設 `VIEWER`（DB `server_default` + ORM `default`）。新建 admin 一律唯讀，需明確升權；**唯一例外**：seed 初始 admin 佈為 `SUPER_ADMIN`（否則無人能升別人的權）。

**理由**：對齊 jwt-role D6/D8 的最小權限與 fail-safe——新實體、缺值一律落到最低權限，絕不預設高權。升權是明確、可稽核的動作。

**取捨**：seed / migration 需記得把初始 admin 拉到 SUPER_ADMIN（規格 §3.3/§5.6 已明列），否則系統啟動即「全員 VIEWER、無人能管理」。這是刻意的一次性 bootstrap 步驟。
