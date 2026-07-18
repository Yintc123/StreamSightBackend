# 規格書：WebSocket 模組（Admin 即時推播）

> 狀態：**Draft（設計，待實作）** ／ 目標版本：next+3 ／ 開發模式：**嚴格 TDD（見 `CLAUDE.md`）**
>
> **語言**：繁體中文。
>
> 🔗 依賴既有機制：JWT（`app/core/auth/jwt.py`：`decode_token`／`extract_role`／`extract_grade`）、`Role`（0=user／1=admin）與 `AdminRole`（super_admin／editor／viewer）、principals supertype、Redis（`app/core/redis`，測試以 `fakeredis`）、`Admin.is_active` 計算屬性、`ADMIN_ROLE_RANK`（[`rbac.md`](./rbac.md)）。
>
> ⚠️ **範圍**：本規格定義 WebSocket **基礎模組**（連線生命週期、認證、連線註冊、跨實例 fan-out、訊息封套協定、心跳、背壓、關閉碼、測試）。**具體業務主題（topic）與其 payload 另立規格**——本文只提供可擴充的推播骨架與一組控制訊息。
>
> 🧩 **前置依賴（本模組實作前需先落地）**：access token 新增 **`sid` claim**（= 該登入的 refresh `family_id`），供 WS 綁 session、支援**單一 logout 精準斷線**（§2.5）。屬 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md)／[`rbac.md`](./rbac.md) 的 JWT 小幅擴充（`create_access_token` 加 `sid` 參數、`login`/`admin_login`/`refresh` 帶入當次 `family_id`）。

---

## 0. 功能總覽（先讀這裡）

**一句話**：讓**已認證的 admin**（`role=1`，任一 `admin_role` 等級）建立長連線，由**伺服器主動推播**即時資料／通知到前端；client 僅送少量控制訊息（訂閱／退訂／心跳）。多實例部署下以 **Redis pub/sub** 做跨實例 fan-out。

**三個定案前提（見 §2）**：

| 維度 | 定案 | 影響 |
|---|---|---|
| 訊息方向 | **Server→Client 推播**為主 | client 端只送控制訊息;協定以「訂閱主題 → 收推播」為核心 |
| 連線者 | **Admin（`role=1`）全等級** | 認證重用 admin JWT;連線後可依 `admin_role` 對敏感主題再授權 |
| 部署規模 | **先支援多實例**（Redis pub/sub），單實例為特例 | `ConnectionManager` 抽象 fan-out;Redis backend 可延後實作但介面先定 |

**端點（暫定）**：`POST /ws/ticket`（HTTP,JWT 認證 → 換 ticket）＋ `GET /ws?ticket=…`（WebSocket upgrade,帶 ticket）。

---

## 1. 背景與目標

CMS 需要「伺服器主動把即時資料/事件推給後台前端」的能力（例如監控看板、即時通知、任務進度）。HTTP 輪詢成本高、延遲大;改用 WebSocket 長連線由 server 主動推播。

### 目標

- 定義 **admin WebSocket 端點**與**連線生命週期**（handshake 認證 → accept → 訂閱 → 推播 → 關閉）。
- **重用既有 JWT 認證**：連線僅限 `role=1`（admin）且帳號 active;認證失敗以 WS 關閉碼拒絕。
- 定義 **`ConnectionManager`**（per-instance 記憶體註冊）與 **`Publisher`**（對 principal／topic 推播），並以 **Redis pub/sub** 抽象跨實例 fan-out。
- 定義 **JSON 訊息封套協定**（控制訊息 + 伺服器推播）與**關閉碼**。
- 定義**心跳／閒置逾時／背壓（慢消費者）**與**撤權／登出即時斷線**（封存/刪除/改密碼/登出）。
- 分層對齊：**API（WS router）→ Service（ConnectionManager／Publisher）→ Redis**;不在 router 內散落業務邏輯。
- **嚴格 TDD**：每個行為先寫失敗測試（`httpx-ws` + `fakeredis`，工具選型見 §7.0-a spike 定案）。

### 非目標（Out of scope）

- **具體業務 topic 與其 payload schema**（監控指標／通知內容…）→ 各自另立規格;本文只給 topic 授權掛勾與封套。
- **User（`role=0`）連線**：本期不做（見 §10 Open Q）;協定設計保留擴充空間。
- **雙向 RPC／client 主動送業務指令**：本期只收控制訊息（subscribe/unsubscribe/ping）。
- **訊息持久化／離線補送（missed-message replay）**：本期為 best-effort 即時推播;持久化另議。
- **GraphQL subscriptions／SSE**：不採（既定用原生 WebSocket）。

---

## 2. 設計決策

### 2.1 兩段式認證：先用 JWT 換 ticket，再用 ticket 建 WS（D1，核心）

**問題**：瀏覽器 `WebSocket` API 無法帶自訂 header（放不了 `Authorization: Bearer`）;若把長命 access token 塞進 WS URL query，會進 log／referrer／代理紀錄，暴露窗口＝整個 token 壽命。

**定案：ticket 換取機制（two-step，Redis-backed、短命、單次）**：

1. **`POST /ws/ticket`**（一般 HTTP，以 `Authorization: Bearer <access_token>` 認證，走既有 `get_current_admin` → 必為 `role=1` 且 active）→ 回一張**短命、單次、opaque** 的 ticket。
2. client 用 ticket 建 WS：`new WebSocket("wss://host/ws?ticket=<ticket>")`。
3. server 在 **accept 前**驗 ticket（見下）→ 解出 `principal_id` → **重載 `Admin` 檢查 `is_active`／讀 `admin_role` 現值** → 通過才 `accept`;失敗 `close(4401)`、不 accept。

**ticket 儲存與消費（Redis，原子單次）**：
- ticket＝`secrets.token_urlsafe(32)` 的 opaque 隨機字串;簽發時 `SET ws:ticket:{ticket} <json: {principal_id, sid}> EX <ttl>`（`ws_ticket_ttl_seconds` **預設 180s／3 分**，進 config;`sid` = 簽發者 access token 的 session id，供 logout 精準斷線，§2.5）。
  - **TTL 的意義＝「拿到 ticket 後多久內要建立 WS 連線」的寬限窗，非連線時長**（連線時長見 §2.2，無硬性上限;票被消費、連線建立後 TTL 即無關）。逾 180s 未連 → 票過期，需重換。正常流程 client 換票後**立即**連 → 票在數毫秒內被消費;TTL 只在 client **延後開連線**時才用得到。
  - （可選 defense-in-depth：存 ticket 的 HMAC hash 而非原值，比照 refresh token pepper 慣例;因單次＋Redis 隔離，風險已低。）
- WS 連線時**原子 get-and-delete**（Redis `GETDEL`，或 Lua）取 principal_id → **保證單次**：同一 ticket 第二次連 → 查無 → `close(4401)`（防重放）。
- **消費後仍重載 Admin 讀現值**，不盲信簽發當下快照 → 授權即時（對齊 rbac R5）。

**為何優於「WS 直接帶 JWT」**：
- ticket **短命（預設 180s）＋ 單次**——即使洩漏在 URL/log，仍無法重放（第一次消費即失效）;正常流程換票後立即連，實際窗口僅數毫秒。
- 長命 access token **永不出現在 WS URL**（只在 ticket 端點的 HTTP header）。
- 天然相容瀏覽器（query param 帶拋棄式 ticket 是安全的），毋須 subprotocol/header 技巧。
- ticket 在簽發當下綁中繼資料（principal_id、`sid`＝session id、可選 Origin/IP）。

> **只 admin 能換 ticket**（`get_current_admin` 保證 `role=1`）;初始 admin（SSM，`sub=0`）持 access token 一樣能換 ticket → 一樣能連（`AuthService.get_admin_from_token` 對 `sub=0` 合成 super_admin）。

### 2.2 連線壽命：無硬性上限，靠心跳＋kick＋定期複查（D2）

- **定案：不設連線最長壽命上限**。使用者可能在網頁停留很久、連線時長難預估;不希望每隔一段就被迫斷線重連。連線持續到以下任一發生才關閉：
  - **心跳判死**（§2.7）：連續未回 `pong` → `close(4000)`。
  - **帳號失效 kick**（§2.5，**主要撤權途徑**）：archive／delete／change_password → Redis kick → `close(4401)`。
  - **背壓**（§2.8）：慢消費者佇列滿 → `close(1013)`。
  - client 主動關閉／實例重啟（`close(1012)`）。
- **兜底：定期複查（防 kick 漏掉）**：每 `ws_reauth_interval_seconds`（預設 300s／5 分，進 config）背景檢查該連線 → **`Admin.is_active=False`（被封存/刪除）** 或 **該連線 `sid` 的 refresh family 已無未撤 token（已登出）** → `close(4401)`。這是「授權讀 child 現值」的低成本安全網（admin 連線數少、每 5 分一次查詢可忽略），同時涵蓋**封存/刪除**（改 `is_active`）與**登出**（不改 `is_active`，靠 session 有效性）兩類。
- **DB session 取得（長連線關鍵）**：WS 端點與定期複查 task **不用 `Depends(get_session)`**——否則 request-scoped session 會綁在整段（無上限）連線壽命上，既佔住連線池（idle-in-transaction），又會與 writer／heartbeat／複查等並發 task 共用單一 `AsyncSession`（**並發不安全**）。改注入 **session 工廠**（`get_session_factory` → `AsyncSessionLocal`），**每個 DB 工作單元（handshake 重載 Admin、每輪複查、可選 subscribe 授權讀現值）各開一個短命 session、用畢即還**；連線閒置（等訊息／心跳）期間**不持有任何 DB connection**。此模式由 WS 專屬 `WsReauthService` 承載（§4），比照 `app/core/db` docstring「非 request 場景自管 session」慣例；既有 HTTP service（`__init__(session)`、多 repo 共享一次 commit）**維持不變** → 兩種 scope 各用對的工具。
- ticket 機制讓 WS 連線**與 access token TTL 解耦**（token 只用來換 ticket、不在 WS 上）;連線壽命由上述機制治理，**不受 access token 30 分 TTL 限制、也無固定上限**。

### 2.3 `ConnectionManager`：per-instance 記憶體註冊（D3）

- 每個 app 實例維護記憶體註冊表：
  - `principal_id → set[Connection]`（一個 admin 可多分頁/多裝置同時連）。
  - `topic → set[Connection]`（訂閱關係;或 `Connection → set[topic]` 反向索引，兩者擇一為主、另一為輔）。
- `Connection` 包一個 Starlette `WebSocket` + 中繼資料（principal_id、`sid`、`cid`、admin_role、連線 id、送出佇列、訂閱集合、last_seen、closed 旗標）。
- 索引：`principal_id → set`、`topic → set`、`sid → set`、`(sid, cid) → Connection`（供 §2.12b 同分頁取代）。
- **職責**：register（含同分頁取代）/unregister、subscribe/unsubscribe、對本實例連線送訊息、`_teardown` 清理死連線（§2.12a）。**不含業務邏輯**（推什麼由 Publisher/呼叫端決定）。

### 2.4 跨實例 fan-out：Redis pub/sub（D4，抽象先定、實作可延後）

- 多實例下，「推給 principal X」或「推給 topic T」的目標連線可能落在**別的實例**。**定案：以 Redis pub/sub 做 fan-out**：
  - 推播端 `Publisher.publish(target, message)` → 發佈到 Redis channel（例：`ws:principal:{id}`、`ws:topic:{name}`、`ws:broadcast`）。
  - 每個實例背景訂閱這些 channel，收到後由本地 `ConnectionManager` 投遞給**本實例**符合的連線。
- **單實例為特例**：`Publisher` 介面不變;可用「本地直送 + 略過 Redis」的 backend，或一律過 Redis（fakeredis 在測試、單一 Redis 在單機）。**規格以多實例為準**，故水平擴展時不需改介面/呼叫端（部署規模未定，見 §0）。
- Redis pub/sub 為 **at-most-once、無持久化**（實例當下沒有對應連線就丟棄）——符合本期「即時 best-effort」定位（持久化/補送見 §10）。

### 2.5 撤權／登出即時斷線（D5）

既有 WS 連線應在下列事件盡快斷開。**機制**：對應 service 除既有動作外，**發佈一則 Redis kick**;各實例收到即關閉本地符合的連線（`close(4401)`）。兩種粒度：

| 事件 | kick channel | 關閉範圍 |
|---|---|---|
| archive／delete／change_password | `ws:disconnect:principal:{id}` | 該 principal 的**全部** WS |
| **logout_all**（登出所有裝置） | `ws:disconnect:principal:{id}` | 該 principal 的**全部** WS |
| **logout**（單一裝置/session） | `ws:disconnect:sid:{family_id}` | **僅該 session** 的 WS |

- **session 綁定（供單一 logout 精準斷線）**：WS 連線記錄其 **session id（`sid` = refresh `family_id`）**。來源：`login`／`admin_login` 簽發 access token 時加 **`sid` claim**（= 該登入的 refresh `family_id`）→ `POST /ws/ticket` 讀 `sid` 存進 ticket → WS 連線帶 `sid`。單一 `logout`（撤某 refresh token）知其 `family_id` → 只 kick 該 sid 的 WS，不誤斷其他裝置。
  - **前置**：access token 新增 `sid` claim（JWT 小幅擴充，見 §10 Q9）。初始 admin（`sub=0`）無 refresh/session、無 `sid` → 只受 principal 級 kick，不受單一 logout 影響（合理:它本就不走 refresh）。
- **兜底（logout 不改 `is_active`，故需擴充複查）**：§2.2 的定期複查**除 `is_active` 外，也驗該連線 `sid` 的 refresh family 是否仍有未撤 token**——若整個 session 已登出（family 無 live token）→ `close(4401)`。如此 logout 即使 kick 漏掉，也會在複查週期內被斷（不必依賴 pub/sub 可靠性）。
  - > **為何不能只靠 kick**：logout 不改 `is_active`、Redis pub/sub 又是 at-most-once;缺了「session 有效性複查」，漏掉的 kick 會讓已登出的 WS 永久存活（連線已無硬性上限）。

### 2.6 訊息封套：JSON、`type` 驅動（D6）

- 所有訊息為 **JSON 物件**，以 `type` 欄位辨識（見 §3）。控制訊息（client→server）與推播（server→client）共用封套形狀但 `type` 值域不同。
- 非 JSON／未知 `type`／超大訊息 → server 回 `error` 或 `close(4400 protocol error)`（見 §3.4）。

### 2.7 心跳與閒置逾時（D7）

- **應用層心跳**：server 每 `ping_interval`（預設 30s）送 `{"type":"ping"}`;client 回 `{"type":"pong"}`。連續 `missed_pong_limit`（預設 2）次未回 → 視為死連線、`close(4000)`。
- 亦處理 WS 協定層 ping/pong（Starlette 自動）;應用層心跳額外用來偵測「TCP 還在但 app 卡住」的半死連線。
- 閒置（無任何訊息）超過 `idle_timeout` 亦關閉。所有時間參數走 config。

### 2.8 背壓：有界送出佇列，慢消費者即斷（D8）

- 每連線一個**有界非同步送出佇列**（`max_queue`，預設 100）。推播寫入佇列;背景 writer task 逐一送出。
- 佇列滿（client 太慢/斷線未偵測）→ **關閉該連線（`close(1013)`）並清理**，避免記憶體無限膨脹拖垮實例。這是刻意取捨：**即時系統寧可斷開慢消費者，不積壓**。

### 2.9 主題授權掛勾（D9）

- 連線本身只要 `role=1 + active`;**個別 topic 可要求更高 `admin_role`**（例：某敏感監控主題限 `super_admin`）。
- `subscribe` 時檢查 `ADMIN_ROLE_RANK[admin.admin_role] >= 該 topic 的最低等級`;不足 → 回 `error`（不關閉整條連線）。**topic → 最低等級的對照表屬業務規格**（本文只定掛勾點與預設「任一 admin 皆可訂閱」）。

### 2.10 分層與模組放置（D10）

```
app/
├── api/routers/ws/router.py        # POST /ws/ticket（換票）+ GET /ws（握手驗票、accept、收控制訊息迴圈）
├── services/ws/
│   ├── ticket.py                  # TicketService（Redis：簽發 + 原子單次消費）
│   ├── manager.py                 # ConnectionManager（per-instance 註冊/投遞）
│   ├── publisher.py               # Publisher（對 principal/topic/broadcast 推播 → Redis）
│   ├── bridge.py                  # Redis pub/sub 背景訂閱 → 本地投遞 + kick 處理
│   ├── reauth.py                  # WsReauthService（持 session 工廠；短命 session 讀 is_active + session 有效性，§2.2）
│   └── protocol.py                # 封套/型別/關閉碼 常數與（de）序列化
└── dtos/ws.py                     # 訊息封套 Pydantic 模型（跨層）
```
- **API 層**只做握手認證、accept、讀控制訊息、委派給 manager/publisher;**業務推播的觸發**由對應 service（未來各業務）呼叫 `Publisher`。
- `bridge` 於 app lifespan 啟動背景 task（訂閱 Redis、投遞、處理 kick）;lifespan 關閉時優雅收斂。
- **lifespan shutdown**：關閉前對本實例全部連線 `manager.close_all(1012)` 優雅斷線（§3.4），再收斂 `bridge`、關 DB/Redis connection。

### 2.11 前置 JWT 擴充：`sid` claim（本模組驅動，需先於 WS 實作落地）

單一 logout 精準斷 WS（§2.5）需要 WS 連線知道自己屬於哪個 session。做法：把 refresh 的 `family_id` 以 **`sid` claim** 帶進 access token，一路流到 WS。

**JWT helper（`app/core/auth/jwt.py`）**：
```python
def create_access_token(subject, role=Role.USER, grade: str | None = None,
                        sid: str | None = None) -> str: ...   # sid 非 None → payload 加 "sid"
def extract_sid(payload: dict) -> str | None: ...             # payload.get("sid")
```
- `sid` 非 None 時才放 key（None → 無 `sid`，向後相容，**比照既有 `grade` 的處理**）。

**簽發端帶入同一 `family_id`（`app/services/auth.py`）**：
- `login` / `admin_login` / `register`：本來就會產一次 `family_id`（`str(uuid4())`）給 refresh;把**同一個** `family_id` 一併傳給 `create_access_token(sid=family_id)`。
- `refresh`：rotation **保持同一 `family_id`**（`rt.family_id`）→ 新 access token 帶 `sid=rt.family_id`。故**同一 session 跨多次 refresh 的 `sid` 不變**（穩定的 session 識別，正是所需）。
- **初始 admin（SSM，`sub=0`）**：`admin_login` 走 access-only、不建 refresh family → **無 `sid`**;其 WS 不受單一 logout 影響（本就不走 refresh），只受 principal 級 kick。

**ticket 端點取 `sid`**：`POST /ws/ticket` 從當次 access token 取 `sid`（`extract_sid(decode_token(token))`，或由認證 dependency 一併回傳）→ 存進 ticket（§2.1）→ WS 連線帶 `sid`。

**語意／邊界**：
- `sid` = `family_id`（uuid4 字串）;非機密（僅 session 識別），可直接用。無 `sid` 的連線（初始 admin、或未帶 sid 的舊 token）→ 不參與 sid-kick，只受 principal 級 kick 與定期複查。

> **跨規格前置**：`sid` 屬 JWT 機制，實作歸 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md)／[`rbac.md`](./rbac.md)（`create_access_token` 已在 rbac §4 因 `grade` 擴充過，`sid` 同型）。**須先落地再做 WS 模組**（見 §8 里程碑）。

### 2.12 連線清理與去重：送出失敗即斷 ＋ 同分頁取代（D11）

**(a) 送出失敗即斷（transport 死亡的確定訊號）**
- writer task 對 `ws.send_json`／心跳 ping 送出時若丟 **`WebSocketDisconnect`／`ConnectionClosed`／`OSError`／已 close 的 `RuntimeError`** → 判定連線已死 → 立即 `_teardown(conn)`。
- `_teardown(conn)`：**冪等旗標（只跑一次）** → `unregister`（清 `principal_id`／`topic`／`sid`／`cid` 索引）→ best-effort `await ws.close()`（socket 已死多為 no-op，吞例外）→ 取消該連線的 writer／heartbeat task。
- **race-safe**：reader（endpoint 的 `receive_json` 迴圈）與 writer 都可能先偵測到死亡;以旗標／鎖保證 `_teardown` 只執行一次，另一方 no-op。
- **只認 transport 例外**：序列化錯（`TypeError` 等）是 bug、只 log、**不可斷連線**（**不要** `except Exception`）。
- **與既有保險分工**：背壓 `1013`（活著但太慢）／心跳 `4000`（TCP 活著但不回 pong）／**本項（send 直接爆＝已斷）**——三者互補、覆蓋整個異常光譜。

**(b) 同分頁取代（防殭屍分頁，鍵＝`(sid, cid)`）**
- 連線鍵＝`(sid, cid)`;`register(new)` 時若已有**同鍵**舊連線 → `old.close(4409)` ＋ `_teardown(old)` → 再註冊 `new`。
- **只取代同一分頁自己的舊連線**（同 `sid` ＋ 同 `cid`），**不影響同一登入的其他分頁**（同 `sid`、不同 `cid`）、也**不會跨分頁 flapping**。
- `cid` 帶法：**WS 連線 query param**（`/ws?ticket=…&cid=<clientId>`），**不進 ticket**（cid 非認證資料、非機密;ticket 只放 `principal_id`+`sid`，§2.1）。**可選**：未帶 `cid` 的連線不參與 same-tab 取代，只靠 (a) ＋ 心跳清理。
- **client 契約**：連線收到 `4409` → **不要自動重連**（已有更新連線;避免與自己較新的連線互踢）。網路類斷線（`4000`/`1013`/`1012`）才走退避重連。

**clientId（`cid`）長什麼樣（前端契約）**：
- **格式**：`crypto.randomUUID()`（RFC 4122 v4 UUID 字串）或等長隨機字串。
- **產生／儲存**：前端在**分頁層級**產一次、存 `sessionStorage`（天生 **per-分頁**：關頁即失、開新分頁必不同）——正好對應「一個分頁一個 `cid`」。
  ```js
  let cid = sessionStorage.getItem("ws_cid");
  if (!cid) { cid = crypto.randomUUID(); sessionStorage.setItem("ws_cid", cid); }
  new WebSocket(`/ws?ticket=${ticket}&cid=${cid}`);
  ```
- **性質**：純**去重鍵**、**非機密、非認證**;server 不信任、不驗身分（亂帶只會取代自己 `sid` 的連線）。server 端只做基本**清洗**（如限 `≤64` 字元、字元集 `[A-Za-z0-9_-]`）防注入/濫用。
- **不要用**：`localStorage`（跨分頁共享 → 所有分頁同 `cid` → 退化成 per-sid、會互踢）、cookie、或任何可反查使用者身分的值（`cid` 不該能識別人）。

---

## 3. 連線與訊息協定

### 3.1 認證流程（兩段式）

**第一段（HTTP，換 ticket）**：
1. client：`POST /ws/ticket`，header `Authorization: Bearer <access_token>`。
2. server：`get_current_admin`（驗 `role=1`＋active）＋ `extract_sid(decode_token(token))` 取 `sid` → 產 `secrets.token_urlsafe(32)` → `SET ws:ticket:{t} {principal_id, sid} EX 180` → 回 `{ "ticket": t, "expires_in": 180 }`。

**第二段（WS handshake，認證於 accept 前）**：
3. client：`new WebSocket("/ws?ticket=<t>&cid=<clientId>")`（`cid` 可選、per 分頁，§2.12b）。
4. server：讀 `query_params["ticket"]` → Redis `GETDEL ws:ticket:{t}`（原子單次）→ 無值 → `close(4401)`;讀 `query_params.get("cid")`（清洗 ≤64 字元、`[A-Za-z0-9_-]`）。
5. 有值 → 解出 `{principal_id, sid}` → 重載 `Admin`（`sub=0` → 合成初始 admin）→ 驗 `is_active`。
6. 通過 → `accept()` → `manager.register`（連線帶 `sid`+`cid`;同 `(sid,cid)` 舊連線 → `close(4409)` 取代，§2.12b）→ 送 `welcome`;失敗 → `close(4401)`（見 §3.4），不 accept。

### 3.2 Client→Server（控制訊息，值域封閉）

| `type` | 欄位 | 語意 |
|---|---|---|
| `subscribe` | `topic: str` | 訂閱主題（授權見 §2.9）;成功回 `subscribed`，不足回 `error` |
| `unsubscribe` | `topic: str` | 退訂 |
| `pong` | — | 回應 server 的 `ping`（心跳） |

> 本期 client **不送業務訊息**;未知 `type` → `error`（不關閉）。

### 3.3 Server→Client（推播/回應）

| `type` | 欄位 | 語意 |
|---|---|---|
| `welcome` | `connection_id`, `admin_role` | accept 後首則;告知連線 id 與自身等級 |
| `subscribed` / `unsubscribed` | `topic` | 訂閱狀態回應 |
| `event` | `topic`, `data`, `ts` | **業務推播**（server→client 的主體;`data` 由各業務規格定義） |
| `ping` | — | 心跳探測（client 須回 `pong`） |
| `error` | `code`, `message` | 非致命錯誤（如訂閱越權、未知 type）;連線續存 |

**封套範例**：
```jsonc
// server → client 推播
{ "type": "event", "topic": "monitor.jobs", "ts": 1730000000, "data": { /* 業務 */ } }
// client → server 訂閱
{ "type": "subscribe", "topic": "monitor.jobs" }
```

### 3.4 關閉碼（WS close code）

| Code | 意義 | 觸發 |
|---|---|---|
| `4401` | Unauthenticated | ticket 無效/過期/已用過、非 admin、帳號 inactive、被 kick |
| `4403` | Forbidden | （保留）連線層等級不足 |
| ~~`4408`~~ | ~~Session expired~~ | **不使用**（無連線硬性上限，§2.2）;帳號失效改由定期複查以 `4401` 關閉 |
| `4400` | Protocol error | 非 JSON／超大訊息／格式錯 |
| `4409` | Connection replaced | 同一分頁（同 `sid`+`cid`）開新連線取代舊連線（§2.12b）;client 收到**不重連** |
| `4000` | Heartbeat timeout | 連續未回 `pong`（§2.7） |
| `1013` | Try again later | 背壓斷線（佇列滿，§2.8）／過載 |
| `1012` | Service restart | 實例關閉（lifespan shutdown 優雅斷線） |

> 4xxx 為 application-defined 範圍（RFC 6455 允許 4000–4999）;1012/1013 為標準碼。

---

## 4. 模組介面（簽名草案）

> 皆 async;實際簽名以實作時 TDD 收斂為準。

```python
# services/ws/ticket.py
class TicketService:
    async def issue(self, principal_id: int, sid: str | None) -> tuple[str, int]:
        """產 opaque ticket、SET ws:ticket:{t} = {principal_id, sid} EX ttl;回 (ticket, ttl)。"""
    async def consume(self, ticket: str) -> tuple[int, str | None] | None:
        """原子 GETDEL ws:ticket:{t};回 (principal_id, sid) 或 None（不存在/已用過/過期）。單次。"""

# services/ws/manager.py
class ConnectionManager:
    async def register(self, conn: Connection) -> None:
        """註冊;若已有同 (sid, cid) 舊連線 → 先 close(4409)+_teardown 取代（§2.12b）。"""
    async def unregister(self, conn: Connection) -> None: ...
    async def _teardown(self, conn: Connection) -> None:
        """冪等清理：unregister（清 principal/topic/sid/cid 索引）+ best-effort close + 取消 task（§2.12a）。"""
    async def subscribe(self, conn: Connection, topic: str) -> None: ...
    async def unsubscribe(self, conn: Connection, topic: str) -> None: ...
    async def send_local(self, *, principal_id: int | None, topic: str | None, message: dict) -> int:
        """投遞給**本實例**符合條件的連線（principal 或 topic 擇一）;回投遞數。有界佇列、慢消費者斷線。"""
    async def kick_local(self, principal_id: int, code: int = 4401) -> None: ...
    async def close_all(self, code: int = 1012) -> None:
        """關閉本實例**全部**連線並清理（lifespan shutdown 優雅斷線，§2.2/§3.4）。"""

# services/ws/publisher.py
class Publisher:
    async def to_principal(self, principal_id: int, message: dict) -> None: ...   # → ws:principal:{id}
    async def to_topic(self, topic: str, message: dict) -> None: ...              # → ws:topic:{topic}
    async def broadcast(self, message: dict) -> None: ...                          # → ws:broadcast
    async def disconnect_principal(self, principal_id: int, code: int = 4401) -> None: ...  # 全部 WS → ws:disconnect:principal:{id}
    async def disconnect_session(self, family_id: str, code: int = 4401) -> None: ...       # 僅該 sid → ws:disconnect:sid:{family_id}

# services/ws/reauth.py — 長連線的 DB 存取（§2.2）：持 session 工廠、每次呼叫開短命 session
class WsReauthService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None: ...
    async def is_connection_valid(
        self, *, principal_id: int, sid: str | None, now: datetime
    ) -> bool:
        """開**短命 session** 讀現值（is_active + session 有效性，§2.2）；用畢即還。
        - principal_id == 0（初始 admin）：合成 super_admin、恆 active；無 sid → 不查 session。
        - 一般 admin：Admin.is_active 為 False → False；sid 非 None 且該 family 已無 live token → False。
        兩條件皆通過才回 True（否則呼叫端 close(4401)）。"""

# services/ws/bridge.py — app lifespan 啟動：訂閱 Redis channel → 呼叫 manager.send_local / kick_local
```

- **DI／lifespan**：`ConnectionManager` 為 per-process 單例（app.state）;`bridge` 背景 task 於 lifespan 啟停;`Publisher` 由 Redis client 建構，供各業務 service 注入。
- **DB session（§2.2）**：新增 `get_session_factory()`（回 `AsyncSessionLocal`，與 `get_session` **並存**）＋ provider `get_ws_reauth_service(factory=Depends(get_session_factory))`（**比照 `get_auth_service` 形狀**，見 `api/dependencies/services.py`）。WS 端點以 `Depends(get_ws_reauth_service)` 取得 service，於 accept 時**捕獲一次**交給背景複查 task 反覆用（背景 task 在 request scope 外，`Depends` 只解析一次）。`Connection` 只存 accept 當下快照的**原生值**（`principal_id`／`admin_role`／`sid`／`is_active`），授權現值一律靠複查重讀，**不掛 ORM `Admin` 物件**（避免 detached／陳舊）。
- **WS 端點**（`api/routers/ws/router.py`）：認證 → accept → `manager.register` → 迴圈 `receive_json`（控制訊息）→ 斷線時 `manager.unregister`;送出由 per-connection writer task 從佇列取。
- **前置依賴（repository）**：定期複查（§2.2）驗 session 有效性需 `RefreshTokenRepository.has_live_tokens_in_family(family_id, *, now)`（唯讀 `EXISTS`，排除已撤銷/已過期）——屬 refresh-token 層，見 [`refresh-token-rotation.md`](./refresh-token-rotation.md) §5.2。

### 4.1 設定（Config，`app/core/config`）

所有 WS 參數走 config、統一 `ws_` 前綴（時間一律「秒」，比照 `jwt_access_token_expire_seconds` 慣例）。**先給預設值、進 `BaseAppSettings`**；實際值待壓測／威脅模型定案（§10 Q6）。無「連線最長壽命」上限（§2.2，刻意不設）。

| 設定鍵 | 預設 | 單位 | 出處 | 意義 |
|---|---|---|---|---|
| `ws_ticket_ttl_seconds` | `180` | 秒 | §2.1 | ticket 簽發後多久內須建立 WS（**換票→開連線的寬限窗，非連線時長**）;逾時票過期需重換 |
| `ws_ping_interval_seconds` | `30` | 秒 | §2.7 | server 送應用層 `ping` 的週期 |
| `ws_missed_pong_limit` | `2` | 次 | §2.7 | 連續未回 `pong` 幾次判死 → `close(4000)` |
| `ws_idle_timeout_seconds` | `120` | 秒 | §2.7 | 連線無任何進站訊息（含 `pong`）超時即關;**須 > `ping_interval`**（否則正常心跳也會被判閒置），建議 ≥ `ping_interval × (missed_pong_limit + 1)` |
| `ws_max_send_queue` | `100` | 則 | §2.8 | per-connection 有界送出佇列上限;滿（慢消費者）→ `close(1013)` |
| `ws_reauth_interval_seconds` | `300` | 秒 | §2.2 | 定期複查週期（`is_active` ＋ session 有效性）;失效 → `close(4401)` |
| `ws_max_connections_per_principal` | `10` | 條 | §6 | 單一 admin 最大同時連線數;超限拒絕新連線（`close(1013)`，過載語意） |
| `ws_max_connections_total` | `10000` | 條 | §6 | 全實例最大連線數（防 DoS）;超限拒絕（`close(1013)`） |
| `ws_max_message_bytes` | `16384` | bytes | §3.4／§6 | 單一進站訊息大小上限;超過 → `close(4400)` |
| `ws_control_msg_rate_limit` | `20` | 則/10s | §6 | 控制訊息速率上限（每連線滑動窗）;超過 → `error`（不斷線）或 `close(1013)` |
| `ws_cid_max_length` | `64` | 字元 | §2.12b | `cid` 清洗上限（字元集 `[A-Za-z0-9_-]`）;超長/非法 → 截斷或不參與 same-tab 取代 |
| `ws_allowed_origins` | `[]`（空＝依環境） | list[str] | §6 | handshake `Origin` 允許清單（防 CSWSH）;不在清單 → 拒絕握手。空清單的語意（全拒/全放/僅同源）於實作時定案，見 §10 Q6 |

> **關閉碼補充（對齊 §3.4）**：資源上限類（per-principal／total 連線數、速率過載）採 **`1013`（try again later，過載）**;訊息協定類（超大／非 JSON／格式錯）採 **`4400`**;`Origin` 不符為 **handshake 階段拒絕**（不 accept、無 application close code）。§3.4 關閉碼表為權威，本表僅標註各 config 觸發的碼。

---

## 5. 流程圖

```
連線（兩段式認證）：
  ① POST /ws/ticket  (Authorization: Bearer JWT)
       → get_current_admin(role==1, active) → SET ws:ticket:{t}=pid EX 180 → 回 {ticket}
  ② WS upgrade  /ws?ticket=<t>
       → GETDEL ws:ticket:{t} ─ 無值 → close(4401)      （單次、防重放）
       → 重載 Admin(pid)：active? ─ 否 → close(4401)
       → accept → manager.register → 送 welcome

訂閱與推播：
  client: {subscribe, topic}
    → 授權(admin_role≥topic 門檻)? ─ 否 → error（連線續存）
    → manager.subscribe → {subscribed, topic}
  業務 service: Publisher.to_topic("monitor.jobs", {...})
    → Redis PUBLISH ws:topic:monitor.jobs
    → 各實例 bridge 收到 → manager.send_local(topic=...) → 佇列 → writer → client {event}

心跳／背壓／複查（無連線硬性上限）：
  每 30s server {ping}；client {pong}；連續未回 → close(4000)
  佇列滿（慢消費者）→ close(1013)
  每 5 分背景複查 Admin.is_active；失效 → close(4401)

撤權／登出即時斷線：
  archive/delete/change_password/logout_all → Publisher.disconnect_principal(pid)
    → Redis ws:disconnect:principal:{pid} → 各實例 kick_local(全部) → close(4401)
  logout（單一 session）→ Publisher.disconnect_session(family_id)
    → Redis ws:disconnect:sid:{fid} → 各實例 kick_local(該 sid) → close(4401)
  兜底：每 5 分複查 is_active + session 有效性 → 失效 → close(4401)
```

---

## 6. 安全性考量

- **ticket 換取（不把長命 JWT 放上 WS）**：長命 access token 只在 `POST /ws/ticket` 的 HTTP header;WS URL 只帶**短命（預設 180s）、單次**的 ticket，即使入 log/URL 也無法重放（Redis `GETDEL` 原子單次，第一次消費即失效;正常流程換票後立即連、實際窗口數毫秒）。認證前不 accept，未授權連線不佔資源。
- **僅 admin**：`role != 1` 一律拒（4401）;連線後敏感 topic 再依 `admin_role` 授權（§2.9）。
- **授權讀 child 現值**：連線期間降權/封存即時反映（kick 或後續授權失敗），不盲信 token claim。
- **無連線硬性上限**（使用者可長時間停留）;撤權靠 **kick**（§2.5）＋**定期 `is_active` 複查**（§2.2，預設 5 分）＋**心跳判死**;access token 只用於換 ticket、不在 WS 上，故其 TTL 不限制連線。
- **Origin 檢查**：WS 無同源政策保護，須在 handshake 驗 `Origin`（允許清單走 config），防跨站 WebSocket 劫持（CSWSH）。
- **資源上限（防 DoS）**：per-principal 最大連線數、全實例最大連線數、單訊息大小上限、控制訊息速率限制;超限拒絕/關閉。
- **背壓不積壓**：慢消費者即斷（§2.8），避免記憶體耗盡。
- **死連線與殭屍清理**：送出失敗即 `_teardown`（§2.12a）;同分頁重連取代舊連線（§2.12b），防半開 TCP/重複 connect 累積殭屍。`cid` 為客戶端去重鍵、server 不信任並清洗（`≤64`、`[A-Za-z0-9_-]`），亂帶只影響自己 `sid`。
- **不外洩**：`error`/close reason 不夾帶敏感內容;server 日誌遮蔽 token（沿用既有 masking 慣例）。

---

## 7. TDD 測試計畫（先寫、先看到 RED）

### 7.0-a 測試工具與 fixture（**spike 已驗證定案**）

> 下述工具選型經一支拋棄式 spike 實測（真實 `create_app()` + 會碰 DB/Redis 的 WS 端點、5 案全綠）後定案。**採 `httpx-ws`，不採 Starlette `TestClient`。**

**選型：`httpx-ws`（`ASGIWebSocketTransport`），全 async、同 event loop。**

| 面向 | Starlette `TestClient`（不採） | **`httpx-ws`（採用）** |
|---|---|---|
| 測試型態 | 同步 `def`、消費 async fixtures（脆弱、官方不建議） | **`async def`**，與既有測試一致 |
| event loop | 獨立 thread/loop（僅因 aiosqlite 寬容才沒炸，脆弱） | **與 fixtures 同一 loop**，同既有 `client` fixture 模型 |
| 共用 `db_session` | 跨 loop、依賴 SQLite 特性 | **乾淨共用**，與 HTTP 測試完全一致 |
| 兩段式認證（HTTP 換票→WS 連線） | 做不到單一 client，`/ticket` POST 需另開同步 client | **同一 client 打 HTTP + WS**（§2.1 流程的決定性優勢，已驗證真實 `POST /admin/auth/login` + WS） |
| 其他 | `StarletteDeprecationWarning` | 無警告 |

**依賴**：`httpx-ws` 加進 `pyproject.toml` `[dependency-groups].dev`（spike 版本 `0.9.0`，連帶 `wsproto`），`uv sync`。**不需** `websockets`（httpx-ws 走 in-process ASGI，非真實網路 WS）。

**新增 `ws_client` fixture（`tests/conftest.py`）**——沿用既有 `client` 的 override 模型，只換 transport：
```python
@pytest.fixture
async def ws_client(
    db_session: AsyncSession, fake_redis: redis.Redis
) -> AsyncGenerator[AsyncClient]:
    from httpx_ws.transport import ASGIWebSocketTransport
    app: FastAPI = create_app()

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_redis] = lambda: fake_redis

    # 長連線 session（§2.2/§4）：WS 端點與複查 task 走 get_session_factory，
    # 測試須 override 成「回一個 yield 共享 db_session 的工廠」，複查才讀得到 fixture。
    @contextlib.asynccontextmanager
    async def _shared_session() -> AsyncGenerator[AsyncSession]:
        yield db_session  # 不 close：交回 db_session fixture 收尾

    app.dependency_overrides[get_session_factory] = lambda: _shared_session

    transport = ASGIWebSocketTransport(app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
```
用法（兩段式，**同一 client**）：
```python
from httpx_ws import aconnect_ws
resp = await ws_client.post("/ws/ticket", headers={"Authorization": f"Bearer {access}"})
ticket = resp.json()["ticket"]
async with aconnect_ws(f"http://test/ws?ticket={ticket}&cid=tab-1", ws_client) as ws:
    welcome = await ws.receive_json()
```

**已驗證可行（spike 結果，供撰寫測試時參照）**：
- `fake_redis` 支援 **pub/sub**（§7.4 fan-out：兩個 `ConnectionManager` 共用同一 `fake_redis` 即可模擬兩實例）與 **`GETDEL`**（§7.0 ticket 單次消費）。
- **close code 讀得到**：server `close(4401)` → client 端 `httpx_ws.WebSocketDisconnect.code == 4401`（§7.1 拒絕類斷言可寫）。
- admin token 以既有 `admin` fixture + `POST /admin/auth/login` 取得（見 `tests/integration/test_admin_auth_api.py`）。

**撰寫 WS 測試的三個陷阱（spike 踩過、務必照做）**：
1. **斷線例外可能被 anyio 包成 `ExceptionGroup`** 在 `async with aconnect_ws(...)` 邊界拋出。需一個小 helper 從 group 遞迴挖出 `WebSocketDisconnect.code`（或用 `except*`）——別只 `except WebSocketDisconnect`。
2. **要讀到 `close(4401)`，server 必須先 `accept()` 再 `close()`**（可先送 `welcome` 再關）。WS application close code（4xxx）只在 upgrade 成功後有效；**accept 前拒絕**在真實瀏覽器是握手層失敗（非 4401 close frame，見 §3.4／§6 對 Origin 與資源上限的「handshake 拒絕」語意）。
3. **時間相關測試**（心跳 §7.3、複查 §7.3/§7.5）**不要 `sleep`**：把 `ws_ping_interval_seconds`／`ws_missed_pong_limit`／`ws_reauth_interval_seconds` 等（§4.1）以極小值覆寫（monkeypatch settings／清 `get_app_settings` cache），或直接寫入過期 `expires_at`／已撤銷 `revoked_at` 的 `RefreshToken` 列來構造 session 失效——沿用 refresh-token-rotation §8 的既有慣例。
4. **長連線 session 別用 `Depends(get_session)`**：WS 端點／複查 task 走 `get_session_factory`（§2.2／§4）。測試須**額外 override `get_session_factory`**（如上 fixture）回一個 yield 共享 `db_session` 的工廠；否則複查 task 會開到**另一條 connection**（SQLite in-memory 為另一個 DB）→ 讀不到 fixture、複查測試失真。此為 §7.5 複查斷線測試的必要地基。

### 7.0 ticket 端點（integration + unit）
- `POST /ws/ticket`：合法 admin JWT → 200 + `{ticket, expires_in}`;無/壞 JWT／`role=0`／inactive → 401/403（沿用 `get_current_admin`）。
- `TicketService.consume`：有效 ticket → 回 principal_id;**第二次消費同一 ticket → None**（單次、防重放）;過期（TTL 到）→ None。

### 7.1 WS 認證（integration）
- WS 帶**無 ticket／亂 ticket／已用過的 ticket** → 連線被拒（`close 4401`）。
- 換到合法 ticket（super/editor/viewer 皆可）→ WS accept、收到 `welcome`。
- ticket 簽發後該 admin 被封存 → 用該 ticket 連 → 重載 Admin 見 inactive → `close 4401`（驗「消費後讀現值」）。
- 初始 admin（`sub=0`）換 ticket → 亦可連（合成 super_admin）。

### 7.2 訂閱與推播（integration）
- `subscribe` 合法 topic → `subscribed`;`Publisher.to_topic` → 該連線收到 `event`。
- 越權 topic（viewer 訂 super_admin-only）→ `error`、連線續存。
- `to_principal` → 該 admin 的**所有**連線都收到（多連線）。
- `unsubscribe` 後不再收該 topic。

### 7.3 心跳／背壓／到期（unit + integration）
- server 送 `ping`;未回 `pong` 逾限 → `close 4000`。
- 送出佇列填滿（模擬慢消費者）→ `close 1013`。
- 定期 `is_active` 複查：連線中的 admin 被封存後，於複查週期內 → `close 4401`（無硬性連線上限，改以複查兜底）。
- **lifespan shutdown**：`manager.close_all(1012)` → 本實例全部連線收到 `close 1012`（Service restart）且索引清空（§2.2/§3.4）。

### 7.4 跨實例 fan-out（unit，fakeredis）
- `Publisher.to_topic` → Redis PUBLISH 對應 channel;`bridge` 收到 → `manager.send_local` 投遞（以兩個 manager 模擬兩實例共用一個 fakeredis）。
- `disconnect_principal(pid)` → `ws:disconnect:principal:{pid}` → `kick_local` 關閉該 principal 全部連線;`disconnect_session(fid)` → `ws:disconnect:sid:{fid}` → 僅關該 sid 連線。

### 7.5 撤權／登出即時斷線（integration）
- 連線中的 admin 被 `archive`／`delete`／`change_password` → 該連線 `close 4401`。
- **`logout_all`** → 該 admin 的**全部** WS 連線 `close 4401`。
- **單一 `logout`**（撤某 session 的 refresh）→ **僅該 `sid` 的 WS** 斷（`close 4401`），**其他 session 的 WS 不受影響**。
- **兜底複查**：logout 後即使不送 kick（模擬 kick 漏掉），複查週期內該連線因「session 無 live refresh token」被 `close 4401`。

### 7.6 資源上限／協定
- 超過 per-principal 最大連線數 → 拒絕/關閉。
- 非 JSON／未知 `type`／超大訊息 → `error` 或 `close 4400`。
- 錯 `Origin` → handshake 拒絕。

### 7.7 連線清理與去重（§2.12）
- **送出失敗即斷**：`ws.send_json` 丟 `WebSocketDisconnect` → 該連線 `_teardown`、四索引（principal/topic/sid/cid）清乾淨、task 結束。
- **冪等**：reader 與 writer 同時偵測死亡 → `_teardown` 只跑一次。
- **不誤斷**：`send_json` 丟 `TypeError`（壞 payload）→ 連線**不斷**、只 log。
- **同分頁取代**：同 `(sid, cid)` 開第二條 → 第一條 `close 4409` 且被清理;新連線正常。
- **不誤殺兄弟分頁**：同 `sid`、不同 `cid` 兩條 → **並存**、互不影響。
- **cid 清洗**：超長/非法字元的 `cid` → 拒絕或截斷（不注入索引鍵）。

---

## 8. 實作順序（TDD 里程碑）

0. **前置（JWT 層 + refresh repo，先做）**：
   - `create_access_token` 加 `sid` 參數 + `extract_sid`;`login`/`admin_login`/`register`/`refresh` 帶入當次 `family_id`（§2.11）。**規格已落於** [`rbac.md`](./rbac.md) §4.1（介面）／§5.1（簽發串接）／§8.1、§8.3（測試）——WS 依賴它。
   - `RefreshTokenRepository.has_live_tokens_in_family(family_id, *, now)`（定期複查 session 有效性用，§2.2）。**規格已落於** [`refresh-token-rotation.md`](./refresh-token-rotation.md) §5.2／§8.2。
1. `dtos/ws.py` 封套模型 + `protocol.py` 型別/關閉碼常數 + **`ws_*` config 進 `BaseAppSettings`（§4.1）** + **測試地基：`httpx-ws` 進 dev 依賴、`ws_client` fixture 進 conftest（§7.0-a，spike 已驗證）**（7.6 部分）。
2. `TicketService`（Redis 簽發 `{principal_id, sid}` + 原子單次 `GETDEL` 消費）+ `POST /ws/ticket` 端點（取 `sid`）（7.0）。
3. WS 端點 handshake 驗票（`GETDEL` ticket → 重載 Admin：role=1 + active → accept；否則 close 4401）+ `welcome`;連線記 `principal_id` + `sid`。**新增 `get_session_factory`（§2.2/§4），handshake 重載 Admin 以短命 session 進行、不用 `Depends(get_session)`**（7.1）。
4. `ConnectionManager`：register（含同 `(sid,cid)` 取代 §2.12b）/unregister/subscribe/unsubscribe/`send_local` + 有界佇列 writer + `_teardown`（送出失敗即斷 §2.12a）（7.2/7.3/7.7）。
5. 控制訊息迴圈：subscribe/unsubscribe（含 topic 授權）/pong;未知 type → error（7.2/7.6）。
6. 心跳（ping/pong + timeout）與定期複查（`is_active` ＋ session 有效性;無連線硬性上限）;**`WsReauthService`（持 `get_session_factory`、每輪開短命 session）＋ provider `get_ws_reauth_service`，複查 task accept 時捕獲、request scope 外反覆用（§2.2/§4）**（7.3）。
7. `Publisher` + `bridge`（Redis pub/sub fan-out、kick）+ lifespan 啟停（含 shutdown `manager.close_all(1012)` 優雅斷線）（7.4）。
8. 撤權／登出即時斷線：archive/delete/change_password/logout_all → `disconnect_principal`;單一 logout → `disconnect_session`（用 §2.11 的 `sid`）;複查已驗 session 有效性（7.5）。
9. 資源上限 + Origin 檢查 + 速率限制（7.6、§6）。
10. 提交前檢查全綠（ruff / ruff format / pyright / pytest）;真 Redis/多實例煙霧測試。

---

## 9. 已定案決策

- ✅ **兩段式 ticket 認證**：`POST /ws/ticket`（JWT/`get_current_admin` 認證）換**短命（預設 180s）、單次、Redis-backed** ticket → WS `?ticket=` 連線,accept 前 `GETDEL` 驗票並重載 Admin 讀現值。長命 JWT 不進 WS URL;防重放。TTL＝換票→開連線的寬限窗（非連線時長）。只 admin（`role=1`）全等級可連。
- ✅ **Server→Client 推播為主**;client 僅送控制訊息（subscribe/unsubscribe/pong）。
- ✅ **無連線硬性上限**（使用者可長時間停留）;連線壽命由**心跳判死＋帳號失效 kick（§2.5）＋定期 `is_active` 複查（§2.2，預設 5 分）**治理;與 access token TTL 解耦（token 只用於換 ticket）。
- ✅ **`ConnectionManager`（per-instance）+ `Publisher` + Redis pub/sub fan-out**;規格以**多實例**為準、單實例為特例（部署規模未定）。
- ✅ **JSON 封套、`type` 驅動**;應用層心跳（ping/pong）+ 閒置逾時;**有界佇列、慢消費者即斷**（1013）。
- ✅ **撤權／登出即時斷線**：archive/delete/change_password/**logout_all** → kick 該 principal 全部 WS;**單一 logout** → 僅斷該 session（`sid`=refresh family_id）WS;定期複查驗 `is_active`＋session 有效性作兜底（logout 不改 `is_active`，不能只靠 kick）。皆 `close(4401)`、授權讀 child 現值。
- ✅ **前置依賴（已定，規格已落地）**：access token 新增 **`sid` claim**（= refresh `family_id`）——單一 logout 精準斷 WS 的必要條件;本模組實作前，先於 JWT 層落地 `create_access_token(..., sid=)` 並讓 `login`/`admin_login`/`register`/`refresh` 帶入當次 `family_id`（**規格見 [`rbac.md`](./rbac.md) §4.1／§5.1**）。另需 `RefreshTokenRepository.has_live_tokens_in_family`（複查 session 有效性，**規格見 [`refresh-token-rotation.md`](./refresh-token-rotation.md) §5.2**）。所有 `ws_*` 參數彙整於本文 **§4.1**。
- ✅ **長連線 DB session 策略（§2.2／§4）**：WS 端點與複查 task 注入 **session 工廠**（新增 `get_session_factory` → `AsyncSessionLocal`；provider `get_ws_reauth_service` **比照既有 `get_*_service`**），**每 DB 工作單元開短命 session、閒置零持有**;**不用 `Depends(get_session)`**（避免 request-scoped session 綁死無上限連線 ＋ 並發共用單一 `AsyncSession`）。授權現值靠複查重讀、`Connection` 只存原生值快照（不掛 ORM）。既有 HTTP service（session 注入、多 repo 共享交易）**不變** → 兩種 scope 各用對的工具（`app/core/db` docstring 已預留 worker 自管 session）。**不新增 config**（沿用 `ws_reauth_interval_seconds`）。
- ✅ **連線清理與去重（§2.12）**：(a) **送出失敗即斷**——writer/心跳 send 丟 transport 例外 → 冪等 `_teardown`（只認斷線例外、不誤斷 payload bug）;(b) **同分頁取代**——鍵 `(sid, cid)`，同分頁重連 `close(4409)` 取代舊連線，不誤殺兄弟分頁、不 flapping。`cid`＝前端 per-分頁 `crypto.randomUUID()` 存 `sessionStorage`、走 WS query（不進 ticket）、可選、server 清洗不信任。
- ✅ **topic 授權掛勾**（`admin_role` 門檻，越權只 `error` 不斷線）;Origin 檢查、資源上限防 DoS。
- ✅ 分層：API（握手/收控制訊息）→ Service（manager/publisher/bridge）→ Redis;業務推播由各 service 呼叫 `Publisher`。
- ✅ **測試工具（spike 已驗證，§7.0-a）**：採 **`httpx-ws`（`ASGIWebSocketTransport`）**、全 async、與 fixtures 同一 event loop，**不採** Starlette `TestClient`（同步、跨 loop、無法單一 client 跑兩段式認證）。新增 `ws_client` fixture（沿用既有 override 模型）;`fake_redis` 已證實支援 pub/sub＋`GETDEL`;close code、兩段式 HTTP＋WS 流程均已實測可寫。

## 10. 待確認事項（Open Questions）

1. **具體業務 topic 與 payload**：`event.data` 的 schema、topic 命名空間（如 `monitor.*`）、各 topic 的最低 `admin_role` 門檻——**另立業務規格**。
2. **User（`role=0`）連線**：本期只 admin;未來若要對 user 推播，需 user 版端點/授權（可能 `/ws`）與 topic 隔離設計。
3. **離線補送／持久化**：本期 at-most-once、無 replay。若需「重連補missed events」，要引入 per-topic 序號 + Redis Stream/儲存（非 pub/sub）。
4. **Redis backend 取捨**：pub/sub（簡單、at-most-once）vs Redis Stream（可回放、consumer group）——依 #3 需求定;介面（`Publisher`）已抽象、可換。
5. ~~**token 傳遞**~~ → **已定案：ticket 換取機制**（§2.1）。ticket 走 query param（因其短命單次而安全）;若 CDN/代理連 query 都記錄且有疑慮,可改由 subprotocol 帶 ticket（同一機制、換傳輸位置）。ticket TTL（**預設 180s**）與是否存 hash 待壓測/威脅模型定案。
6. **心跳/佇列/上限/複查的實際參數**（**已彙整並給定預設於 §4.1**：`ws_ping_interval_seconds`／`ws_missed_pong_limit`／`ws_idle_timeout_seconds`／`ws_max_send_queue`／`ws_reauth_interval_seconds`／`ws_max_connections_per_principal` 等;**無連線硬性上限**）——**鍵名與預設已定案**，僅實際數值待壓測調校;另 `ws_allowed_origins` 空清單語意（全拒/全放/僅同源）待實作定案。
7. **可觀測性**：連線數/推播量/斷線原因的 metrics 與結構化 log 欄位（沿用既有 logging）待補。
8. **降權即時性強化**：無連線硬性上限,撤權靠 kick + 定期複查（預設 5 分）;複查間隔 `ws_reauth_interval_seconds` 待定（更短＝更即時但更多查詢）;是否在敏感推播前再讀 child 現值亦待定。
9. ~~JWT `sid` claim 要不要做~~ → **已定案：做（單一 logout 粒度已納入本期）**;**介面／簽發串接／測試規格已落於 [`rbac.md`](./rbac.md) §4.1／§5.1／§8**。待確認的僅剩：`sid` 直接用 `family_id`（uuid4 字串）即可，是否另需 opaque 化（避免外洩 family_id）——傾向直接用（family_id 本非機密）。
