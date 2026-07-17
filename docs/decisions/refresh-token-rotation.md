# 設計決策：Refresh Token 與 Rotation 機制

本文記錄 refresh token 模組的**關鍵設計決策與取捨**（為什麼這樣設計）。實作細節（資料模型、介面、TDD 測試計畫）見規格書 [`../specs/refresh-token-rotation.md`](../specs/refresh-token-rotation.md)。

相關程式碼（實作後）：`app/core/auth/refresh.py`、`app/models/refresh_token.py`、`app/repositories/refresh_token.py`、`app/services/auth.py`。

## 目錄

- [D1：refresh token 用 opaque 而非 JWT](#d1refresh-token-用-opaque-而非-jwt)
- [D2：DB 只存 HMAC-SHA256（keyed hash）](#d2db-只存-hmac-sha256keyed-hash)
- [D3：存 DB，而非 Redis、也不塞 identities](#d3存-db而非-redis也不塞-identities)
- [D4：Rotation + Token Family（reuse detection）](#d4rotation--token-familyreuse-detection)
- [D5：Rotation 的「消費」是原子 UPDATE](#d5rotation-的消費是原子-update)
- [D6：Reuse 誤判與 grace 視窗](#d6reuse-誤判與-grace-視窗)
- [D7：透過 response body 傳遞，不用 cookie/session](#d7透過-response-body-傳遞不用-cookiesession)
- [D8：過期 token 的清理策略](#d8過期-token-的清理策略)

---

## D1：refresh token 用 opaque 而非 JWT

**決策**：refresh token 是一段高強度隨機字串（`secrets.token_urlsafe(32)`，≈256 bits 熵），對 client 不可解析，伺服器靠查 DB 判斷有效性。它**不是 JWT**。access token 仍維持 JWT。

**脈絡**：本專案兩種 token 職責不同——

| | Access token | Refresh token |
|---|---|---|
| 型態 | JWT（自帶簽章 claims） | Opaque（隨機字串，DB 為真實來源） |
| 驗證 | 無狀態：驗簽 + 檢查 `exp`，不查 DB | 有狀態：查表比對 hash 與狀態 |
| 效期 | 短（預設 1800s） | 長（預設 14d） |
| 即時撤銷 | 否（靠短效期過期） | **是**（改 DB `revoked_at`） |

**理由**：refresh token 長效、外洩風險高，必須能被伺服器**即時撤銷**——rotation、logout、reuse 連坐、logout-all 全都依賴這點。JWT 無狀態、簽出去到期前無法作廢，除非額外維護黑名單（等於又回到查 DB，失去 JWT 意義）。opaque token 的真實來源就是 DB，撤銷＝改一個欄位。access token 則刻意維持 JWT + 短效期，換取「驗證不查 DB」的效能。

**取捨**：refresh 每次都要查 DB（有狀態成本），換來可控可撤銷——對長效憑證而言值得。

---

## D2：DB 只存 HMAC-SHA256（keyed hash）

**決策**：DB 不存 token 明文，只存 **HMAC-SHA256(key=pepper, msg=token)** 的 hex digest。pepper 由設定 `refresh_token_hash_secret`（`SecretStr`，≥32 字元）提供，與 `jwt_secret_key` 分離。

**理由**：
- 對齊本專案「credential 不落地明文」原則（同 [`salt-and-iv.md`](./salt-and-iv.md) 的思路）。
- 用 **keyed hash（HMAC）而非裸 SHA-256**：即使 DB 單獨外洩，攻擊者缺 pepper 也無法離線建表反查或偽造 lookup。
- token 本身已是高熵隨機值，不需 argon2 這類慢雜湊（那是為了防低熵密碼被暴力破解，見 [`argon2-gil.md`](./argon2-gil.md)）；HMAC-SHA256 快且足夠，輸出 64 字元 hex，storage 不變。

**取捨**：多一個需保管的祕密（pepper）；與簽章金鑰分離是為了職責清楚，代價是多一個 env 變數。

---

## D3：存 DB，而非 Redis、也不塞 identities

**決策**：refresh token 落 **DB**（新增 `refresh_tokens` 表）。

**為何不用專案已有的 Redis？** refresh token 需要「依 `family_id` 連坐撤銷」與「依 `user_id` 全撤（logout-all）」這類**關聯查詢**；14 天長效憑證需要**持久性**（不能因 Redis flush/重啟就把所有人登出）；rotation 又需與撤銷在**同一交易**中一致完成。這些 DB 一次到位。若放 Redis 得自維 `family→tokens`、`user→tokens` 兩組二級索引、自行處理原子性與持久化，複雜且脆弱。Redis 仍適合「短效、可重建、無關聯」的 cache（見 [`redis-keys-scan.md`](./redis-keys-scan.md)），與此情境不同。

**為何不塞 `identities` 表？** `identities` 是「登入方式」，一個 (user, provider) 一列且需長存（見 [`identity-constraints.md`](./identity-constraints.md)）；refresh token 是「可多筆、會過期、會輪替」的 session 憑證，語意不同，獨立成表較清晰，也利於未來的裝置管理。

---

## D4：Rotation + Token Family（reuse detection）

**決策**：每次成功 refresh 都**撤銷舊 token、發新 token**（單次使用），同一登入 session 的輪替鏈共用一個 `family_id`，並以 `replaced_by_id` 串起鏈結（audit）。若呈上「存在但已撤銷」的 token（超過 grace，見 D6），判定外洩 → **撤銷整個 family** 並回 401。

**理由**：單次使用縮短被竊 token 的可用視窗；family 連坐讓「舊 token 再現」能一次廢掉整條 session——攻擊者即使搶用偷來的 token，一旦真正使用者再輪替就觸發連坐，雙方都被踢出、需重新登入。這是業界（如 Auth0）refresh token rotation 的標準模型。

---

## D5：Rotation 的「消費」是原子 UPDATE

**決策**：「撤銷舊 token」用單一條件式原子 UPDATE——`UPDATE ... SET revoked_at=..., replaced_by_id=... WHERE id=:id AND revoked_at IS NULL`，檢查 `rowcount == 1`；只有搶到的請求繼續發新 token。

**脈絡/理由**：若用「先讀出、判斷 active、再寫回」的 read-then-write，正式 DB（Postgres）上同一個 token 被**並發**送兩次（前端重試、雙擊）時，兩個請求可能都讀到 active → 各自發一個 child，rotation 鏈分岔、reuse detection 失準。原子 UPDATE 讓「消費」具原子性與冪等性，並發下只有一個贏家。

**取捨**：SQLite（測試）看不出並發差異，此決策主要保護 Postgres 正式環境；需搭配並發語意的測試佐證。

---

## D6：Reuse 誤判與 grace 視窗

**決策**：引入 grace 視窗（設定 `refresh_token_reuse_grace_seconds`，預設 10 秒）。呈上的已撤銷 token 若在 `now - revoked_at <= grace` 內剛被撤銷，視為良性並發/重試 → **只回 401、不撤 family**；超過 grace 才判定真正外洩重用 → 撤整個 family。

**脈絡/理由**：rotation 有個已知取捨——合法 client 的 refresh 回應若在網路上遺失並用同一舊 token **重試**，會命中 reuse 路徑，可能誤把整條 session 連坐登出。grace 視窗在「安全」與「避免誤殺正常使用者」間取得平衡。無法安全地「重發同一個 child」（伺服器不留 child 明文），故 grace 的行為是「不連坐」而非「補發」。

**取捨**：grace 內若真有攻擊者搶用，會被放行一次（只回 401 不連坐）；視窗短（預設 10s）將風險控制在可接受範圍，且可依上線實測調整。

---

## D7：透過 response body 傳遞，不用 cookie/session

**決策**：refresh token 一律走 **response body**（JSON），client 自行保存並於呼叫 `/auth/refresh`、`/auth/logout` 時放進 request body。**不使用 cookie / session**。

**理由**：本 API server 維持無狀態，與現行 JWT access token 相同的傳遞模型；不引入 cookie 就不需處理 CSRF 與跨網域 cookie 的複雜度。

**取捨**：相較 HttpOnly cookie，body 傳遞對 XSS 的防護較弱，需由 client 端負責安全保存；此取捨符合本專案「純 API、無 session」的定位。

---

## D8：過期 token 的清理策略

**決策**：採 **opportunistic 清理**——於 `login`（低頻事件）成功後 best-effort 呼叫 `delete_expired(now)` 刪除 `expires_at <= now` 的列；清理失敗不影響登入。保留 `delete_expired` 供未來接排程/cron 全表清理。

**理由**：rotation 會持續累積 revoked/expired 列（14 天效期 + 頻繁輪替），需回收否則無界成長。放在 `login` 而非 `refresh` 熱路徑，避免污染高頻操作。仍在效期內的 revoked 列**不刪**（reuse detection 在 grace 之外仍需辨識它們曾存在且已撤銷）。

**取捨**：opportunistic 依賴使用者登入頻率，非活躍帳號的過期列可能滯留；本專案目前無排程器，故先此策略，未來可補 cron。
