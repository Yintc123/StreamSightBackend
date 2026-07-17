# 設計決策：JWT Role 機制與 Admin 角色

本文記錄 JWT role / Admin 模組的**關鍵設計決策與取捨**（為什麼）。實作細節見規格書 [`../specs/jwt-role-and-admin.md`](../specs/jwt-role-and-admin.md)。相關既有決策：[`refresh-token-rotation.md`](./refresh-token-rotation.md)、[`identity-constraints.md`](./identity-constraints.md)、[`argon2-gil.md`](./argon2-gil.md)。

相關程式碼（實作後）：`app/core/enums.py`、`app/core/auth/jwt.py`、`app/models/principal.py`、`app/models/admin.py`、`app/models/refresh_token.py`、`app/api/dependencies/auth.py`、`app/api/routers/admin/`。

## 目錄

- [D1：導入 principals supertype 承載所有帳號身分](#d1導入-principals-supertype-承載所有帳號身分)
- [D2：refresh token 擁有者 = principals.id（保完整性）](#d2refresh-token-擁有者--principalsid保完整性)
- [D3：JWT sub 用全域 principal_id，role claim 快取角色](#d3jwt-sub-用全域-principal_idrole-claim-快取角色)
- [D4：User / Admin 兩張 child 表](#d4user--admin-兩張-child-表)
- [D4b：is_active 留在各 child（只有判別子 role 上移）](#d4bis_active-留在各-child只有判別子-role-上移)
- [D5：Admin 自帶 password_hash，不沿用 Identity](#d5admin-自帶-password_hash不沿用-identity)
- [D6：授權用 dependency、角色不符回 403、fail-safe](#d6授權用-dependency角色不符回-403fail-safe)
- [D7：admin 經 seed 佈建，不公開註冊](#d7admin-經-seed-佈建不公開註冊)
- [D8：向後相容——缺 role claim 視為 0](#d8向後相容缺-role-claim-視為-0)
- [D9：複合 FK 硬化子型別-角色一致性](#d9複合-fk-硬化子型別-角色一致性)
- [D10：交易邊界改用 Unit-of-Work（repository 只 flush）](#d10交易邊界改用-unit-of-workrepository-只-flush)

---

## D1：導入 principals supertype 承載所有帳號身分

**決策**：新增 `principals(id, role)` 父表；`users`、`admins` 各以 `principal_id`（unique FK → principals，`ON DELETE CASCADE`）一對一掛上；`role` 存於 principals。任何「屬於某帳號」的資料（本規格是 `refresh_tokens`）FK → `principals.id`。

**脈絡**：要讓「一張共用 refresh_tokens、共用 rotation」同時服務 User 與 Admin，擁有者必須能指向兩種身分之一。三種做法：

| 做法 | FK 完整性 / CASCADE | 擴充到 N 種身分 | 代價 |
|---|---|---|---|
| `(role, id)` 無 FK 多型（Rails 風） | ❌ 靠 app 層清理 | 易 | 犧牲完整性、有「忘記清就漏洞」footgun |
| 兩個 nullable FK + CHECK | ✅ 各自 CASCADE | 每加一種 +1 欄 | 少數固定身分尚可 |
| **principals supertype（本決策）** | ✅ 單一 FK + CASCADE | ✅ 最佳 | 多一表 + 一層 join、建立兩步 |

**理由**：這個 codebase 一向 integrity-first（刻意用 FK CASCADE、見 [`identity-constraints.md`](./identity-constraints.md)）。supertype 讓**每一段關聯都是真 FK**——`refresh_tokens → principals`、`users/admins → principals` 都由 DB enforce，沒有任何無 FK 的多型欄位。刪 principal 一次連帶清掉 child + identities + refresh_tokens，**根除「忘記清理殘留 token」的安全漏洞**。未來要再加身分（partner / service account…）或任何 principal-owned 表，只要 FK → principals，不需再改結構——這是它勝過「兩個 nullable FK」的關鍵。

**取捨**：多一張 `principals` 表與一層 join；建立帳號變成「先建 principal 再建 child」兩步（同交易）；既有 `users` 需 migration 回填一筆 principal。對只有兩種身分而言這是「稍微超前部署」，但換得完整性 + 乾淨的擴充路徑，符合本專案風格，故採用（而非最省的無 FK 多型）。

**✅ 前提已確認（擴充假設成立）**：supertype 的回報押在「**未來會有 ≥3 種 principal-owned 身分**（例如 partner / service account）或 ≥3 種共用 refresh_tokens 的登入型別」。專案路線圖**已明確預期第三種身分**，故此擴充假設**成立並被接受為前提**——supertype 是刻意且正確的超前部署。較輕的「兩個 nullable FK + CHECK」雖對「只有 User + Admin」能拿到同等的 FK/CASCADE 完整性（少一表、少一層 join、少「role 三份冗餘」，見 D9），但在「確定會擴充」的前提下每加一種身分就要改結構，故不採。（原為 Open Q，見規格 §11 Q5，已定案。）

---

## D2：refresh token 擁有者 = principals.id（保完整性）

**決策**：`refresh_tokens.user_id`（FK → users）改為 `principal_id`（FK → `principals.id`，`ON DELETE CASCADE`）。

**理由**：承 D1——擁有者指向 principal 而非某一 child，一套 rotation / reuse / family / logout-all 同時服務 users 與 admins；`/auth/refresh`、`/auth/logout` 因此角色無關（token 對應一個 principal，refresh 依 `principals.role` 重簽正確 role，天然防提權）。單一 FK + CASCADE 確保無孤兒 token 且刪帳號自動連帶清 token。

**取捨**：refresh 熱路徑為取得角色需一次 `principals` 查詢（PK lookup，便宜）再解析 child 驗 active。可接受；換得完整性與零 app 層清理負擔。

---

## D3：JWT sub 用全域 principal_id，role claim 快取角色

**決策**：access token `sub` = `principal_id`（全域唯一）；另帶 `role` claim（= `principals.role` 的快取）。

**理由**：有了 principals，principal_id 天生全域唯一——`sub` 直接用它，**根本上消除 `users.id`/`admins.id` 撞號的授權風險**（不需再靠 role 去猜該查哪張表）。`role` claim 則讓授權**免 join principals** 即可快速分流（見 D6）。role 由後端登入時寫入、受 JWT 簽章保護，用戶不可竄改。

**取捨**：`sub` 語意從「user.id」改為「principal_id」，既有若有斷言 `sub` 內容的測試需同步（規格 §8.6 已註明）。

---

## D4：User / Admin 兩張 child 表

**決策**：一般使用者（role 0）用 `users`、CMS 管理者（role 1）用新 `admins`，兩者都掛在 `principals` 下，不共用單一 child 表。

**理由**：兩者生命週期與關注點不同——使用者可公開自助註冊、可綁多 OAuth、面向 App；admin 由後台佈建、只密碼、面向 CMS、權責敏感。各自成表讓資料邊界、註冊流程、授權各自乾淨；共通的帳號身分則由 D1 的 principals 抽象。

**取捨**：兩套 repository/service 與兩個登入入口（見 D7）。換得清楚邊界。

---

## D4b：is_active 留在各 child（只有判別子 role 上移）

**決策**：`is_active` 留在 **`users` / `admins` 各自一欄**（不上移 `principals`）；`email`、`name` 亦留 child。父表 `principals` **只承載判別子 `role`**。既有 `user.is_active` / `UserResponse` **零改動**。

**脈絡**：曾考慮把 `is_active`（帳號能不能用）依 class-table inheritance「共有屬性放父表」原則上移到 `principals`，讓「停用任一帳號」是對父表一次 UPDATE、且 `refresh` 一次 `principals` 查詢即得 `role` + `is_active`。但在 **async SQLAlchemy** 下，這個正規化純度是有代價的。

**為何最終留 child**：
1. **免 async footgun**：`is_active` 放父表後，凡讀它的路徑都必須 eager-load `principal`，否則 async 下逐列 lazy load → **`MissingGreenlet` 直接崩潰**（不是慢，是爆）。而讀 `is_active` 的路徑幾乎都是**高頻、已知型別**的 child 存取（`get_current_user`/`get_current_admin`/list users/`UserResponse`）。留 child 讓這些路徑讀「本地欄位」，**結構上免疫** N+1 與 `MissingGreenlet`，無需 `lazy="joined"` 或 `association_proxy`。
2. **N+1 方向其實相反**：把共有欄位放父表，才會讓「從 child 情境讀它」需要 join、忘了就 N+1；留 child 沒有這個風險。
3. **對既有模組改動最小**：`User` 只加 `principal_id`（不移除 `is_active`、不搬遷）；migration 不需回填/DROP `is_active`；上週才上線的 `refresh` 本來就讀 `user.is_active`，維持現狀。

**為何 role 仍上移、is_active 不上移（邊界原則）**：兩者特性不同——
- `role` 是**型別判別子**、建立後**不可變**，且 **refresh 無 role claim、非查父表不可**（角色無關路徑的剛性需求）→ 放父表最自然。
- `is_active` 是**可變的帳號狀態**，讀它的路徑幾乎都**已知型別**（有 JWT role claim 或已在操作特定 child）→ 放 child 讓高頻路徑最省、最安全。
- `email` / `name` 是識別屬性，上移並全域 unique 會禁止「同一 email 同時是 user 又是 admin」，破壞獨立命名空間 → 留 child。

**取捨**：`is_active` 欄位定義在 `users` / `admins` **重複宣告一行**（DRY 的輕微代價，非資料重複）；`refresh` 驗 active 需 `principals`（拿 role）+ child（拿 is_active）**兩次 indexed lookup**（refresh 屬低頻，可忽略）。換得高頻讀取路徑乾淨、async 免 footgun、既有模組近乎零改動。方向正確，故採用（實作細節見規格 §3.3/§5.2/§5.4/§8）。

---

## D5：Admin 自帶 password_hash，不沿用 Identity

**決策**：`admins` 直接放 `password_hash`（argon2id），不建 `AdminIdentity`。

**理由**：`User` 拆 credential 到 `Identity` 是為了多 provider（見 [`identity-constraints.md`](./identity-constraints.md)）；CMS admin 只需密碼登入，套 Identity 的雙 UniqueConstraint 與 provider 欄位是過度設計。直接存 hash 更內聚。密碼雜湊仍 argon2id（見 [`argon2-gil.md`](./argon2-gil.md)）。

**取捨**：未來 admin 要 OAuth 需再引入 AdminIdentity（YAGNI）。

---

## D6：授權用 dependency、角色不符回 403、fail-safe

**決策**：以 FastAPI dependency 表達授權（`get_current_user` 限 role 0、`get_current_admin` 限 role 1、`require_role(...)`、`get_current_principal`）。角色不符回 **403 Forbidden**；缺 role → 預設最低權限 role 0，絕不預設 admin。

**理由**：把「看 role + sub + 解析對應 principal/child + 檢查 active」集中一處，避免各端點漏看 role。403（已認證但越權）與 401（未認證）語意分明。fail-safe 降權符合最小權限。

**fail-safe 的兩個面向（實作硬化）**：
- **`extract_role` 對未知值也降權**：`Role(payload.get("role", Role.USER))` 只處理「缺 claim」；若 claim 是未知整數（未來版本簽出 `role=2` 被舊 server 解到）會 `ValueError` → 500。改為 try/except 包起、未知值回 `Role.USER`，讓「缺 claim」與「未知值」都 fail-safe 到最低權限（見規格 §5.1）。
- **`get_current_principal` 不查 DB**：logout-all 只需 `(principal_id, role)`，兩者都在已驗簽的 token 內；回輕量 `CurrentPrincipal(id, role)` 值物件即可，**免一次 DB 往返**，也不必解析 child / 檢查 is_active（停用帳號仍應能撤自己的 token）。`revoke_all_for_principal` 對不存在的 id 為無害 no-op，故不查 DB 無安全風險（見規格 §5.6）。

**取捨**：admin token 不能存取 user 端點、反之亦然；真有跨角色需求用 `require_role(USER, ADMIN)` 明確放行。

---

## D7：admin 經 seed 佈建，不公開註冊

**決策**：不提供 `/admin/register`；初始 admin 由一次性 seed script（讀環境變數、冪等）建立，後續管理 API 另立規格。

**理由**：CMS admin 高權限，公開自助註冊等於後門。seed 讓「誰能建 admin」受控於部署者。

**取捨**：新增 admin 目前需跑 script（尚無管理 API）——刻意的範圍限制。

---

## D8：向後相容——缺 role claim 視為 0

**決策**：解碼時 `role = payload.get("role", Role.USER)`；沒有 `role` 的舊 token 一律 role 0。

**理由**：預設 0 是對「格式正確、以現行 secret 簽出、但缺 role claim」的 token 的 fail-safe——因 role 0 是最低權限，缺 claim 降到最低權限符合最小權限原則（見 D6）。舊 user access token 天然無 role claim、且本就是 role 0，故此預設**正是正確的向後相容**。

**⚠️ 修訂——`sub` 語意變更的過渡期，由「保留 id」化解，而非 `default-0`／輪替 secret**：`sub` 由 `user.id` 概念上改為 `principal_id`。曾擔心：若 migration 讓 `principal.id` 成為**全新密集序列**，`user.id` 有空洞時 `user.id ≠ principal_id`，舊 token 的 `sub`（= 舊 user.id）會被 `get_by_principal_id` 載入**另一個帳號** → 帳號混淆（水平越權）。此時 `default-0` 放行反而是啟用碰撞的原因。

**結論——採「保留 id」**：migration 以**顯式 id** 回填 principals，令既有 user 的 `principal.id == user.id`（見規格 §3.5 step 2），並把 principals 的 AUTO_INCREMENT 推到 `max(user.id)+1`（新 admin/user 由此之上取號、與既有 user id 空間不撞號）。於是舊 token 的 `sub` 仍解析到**同一個正確 user**，`default-0` 對這些 token 也剛好正確（舊 user = role 0）。**過渡期安全因此不需輪替 `JWT_SECRET_KEY`、使用者無需重新登入**；`default-0` 回歸為單純的向後相容 + fail-safe，不再背負過渡風險。

**取捨**：migration 需以顯式 id 回填並重設 AUTO_INCREMENT——這是一次性、標準的 migration 技法，且**比「新序列 + 視窗函數對齊」更簡單、更安全**（無錯位可能）。換得零 cutover 摩擦：不輪替 secret、不強制重新登入、不賭 id 不相撞。

> 備援：僅當未來某次遷移**無法保留 id**（跨庫重編號等）時，才需回到「cutover 輪替 `JWT_SECRET_KEY` 使舊 access token 失效、client 以 opaque refresh token 換發」的策略。本規格因保留 id 而不需要。

---

## D9：複合 FK 硬化子型別-角色一致性

**決策**：**既定**以複合 FK 在 DB 層擋死「子型別-角色錯配」——`principals` 加 `UNIQUE(id, role)`；`users` / `admins` 各帶一個**常數 `role` 欄**（分別固定 0 / 1），以 `FK (principal_id, role) → principals(id, role)`（`ON DELETE CASCADE`）綁定，再加 `CHECK(role = 0/1)` 釘死常數。如此 `User` 不可能掛到 `role=1` 的 principal（反之亦然），錯配在 DB 層即 `IntegrityError`。

**脈絡**：單純的 `principal_id` FK 只保證「principal 存在」，不保證「型別對得上」——一個 `users` 列理論上可指向 `role=1` 的 principal。原可只靠 service 建立時配對正確，但這與本 codebase 的風格不一致。

**理由**：這個 codebase 一向 **integrity-first**——`identities` 用兩個獨立 UniqueConstraint 讓「schema 直接反映業務規則」（見 [`identity-constraints.md`](./identity-constraints.md)）、處處 FK CASCADE 由 DB 保完整性、refresh token 擁有者刻意用真 FK（見 D2）。在此脈絡下把「型別-角色一致性」退回「靠 service 記得」是前後不一致。`role` 建立後不可變，複合 FK 幾乎零維護成本，且正好把一條業務不變量（「user↔role0、admin↔role1」）用 schema 表達出來。複合 FK 同時附帶擋掉「同一 principal 被 user 與 admin 同時指向」（role=0 的 principal，admin 的 role_const=1 配不上）。

**取捨**：
- 每張 child 多一個「看似冗餘、但永遠是常數」的 `role` 欄，`principals` 多一條 `UNIQUE(id, role)`（`id` 本就唯一，此約束純為當複合 FK 的參照目標）。
- **父表 role 值域也硬化**：child 用 `CHECK(role=0/1)` 釘死，但**父表 `principals.role` 本身若不加約束，DB 仍允許建出 `role=5` 這種無對應 child 型別的 principal**——這是 integrity-first 的漏網。故 `principals` 一併加 `CHECK(role IN (0,1))`（`ck_principals_role_domain`），父子兩側值域一致鎖定（見規格 §3.2/§3.5/§8.2）。代價：新增角色時需改此約束，與 child 的 CHECK 同性質，一致即可。
- **`role` 這個事實會存在四個地方**：`principals.role`（真值）、`users.role`(恆 0)、`admins.role`(恆 1)、JWT `role` claim（快取）。這是本方案在「schema 硬化」光譜上**偏重的一端**——四份中三份是為 DB 層強制而生的冗餘常數。它與 `identity-constraints.md` 的 integrity-first 精神一致、且 `role` 建立後不可變故幾乎零維護，因此**自洽而非過度**；這份重量與 D1 的「supertype 擴充假設」綁在一起評估——該假設**已確認成立**（路線圖預期第三種 principal 身分，見 D1、規格 §11 Q5），故這整套（supertype + 複合 FK + role 三份冗餘）是為「確定會來的擴充」預付的合理投資，而非為兩種身分的過高代價。
- 它**只**顧「型別-角色錯配」；「無懸空 child」仍靠 CASCADE、「無孤兒 principal」仍靠交易原子性（三者分工見規格 §7）。複合 FK 是 defense-in-depth 的一層，不取代 service 於建立時的正確配對。

**取捨方向**：以少量冗餘欄位換 DB 層強保證，符合 integrity-first，故**採用**（原規格 §11 Open Question 已定案；實作見規格 §3.2/§3.3/§3.5/§8.2）。

---

## D10：交易邊界改用 Unit-of-Work（repository 只 flush）

**決策**：repository 一律只 `flush`、**絕不 `commit`**；由 service 的 use-case 方法（`register`、`AuthService.admin_login` 相關建立、`AdminService.create`）持有**唯一一次**交易邊界，讓一個業務動作涉及的多個實體在**同一 commit** 原子落地，失敗整批 rollback。

**脈絡**：本規格要在 `register` 最前面插入 `principals.create()`，若沿用現有「每個 service method 各自 commit」的模式，principal 先 commit、後續失敗 → 孤兒 principal。追查後發現**問題不始於本規格**：現有 `register` 已有**三個獨立 commit 點**（`UserService.create` 自 commit、`identity` add 後 commit、`_issue_refresh_token` 後 commit）——**現況的 register 本就不是原子的**，crash 於中間會留「有 user 無 identity」等半殘狀態。本規格只是讓半殘型態再多一種（孤兒 principal）。

**理由**：把三／四段 commit 收斂成一次，是唯一能同時（a）避免孤兒 principal、（b）修掉既有 register 非原子性、（c）讓 §7 完整性三分工中的「無孤兒 principal＝交易原子性」真正成立的做法。「repository 只 flush、service 持有 commit」是 async SQLAlchemy 的常見 Unit-of-Work 形態，邊界清楚、易測。

**取捨**：需將 `UserService.create` 拆出一個**不 commit** 的建立路徑（`build_user(...)` 或 `create(..., commit=False)`），呼叫端負責 commit；既有直接呼叫 `create` 的路徑需一併檢視。換得「一個業務動作 = 一個交易」的正確語意，並以「register 中途失敗 → 資料庫零殘留」測試守門（見規格 §5.4/§8.4/§9 step 3.5）。

**相關**：此決策雖由本規格觸發，但屬**跨模組的交易邊界原則**，未來新增「一個動作寫多表」的 use-case 應一體遵循。
