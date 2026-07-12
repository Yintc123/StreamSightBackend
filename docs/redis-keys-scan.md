# Redis 模糊查詢:KEYS 與 SCAN

Redis 用 pattern 找 key 的兩種指令,以及為什麼正式環境只能用 `SCAN`。相關程式碼位於 `app/core/redis/`。

## 目錄

- [前置知識:keyspace 是一張 hash table](#前置知識keyspace-是一張-hash-table)
- [KEYS 的原理與風險](#keys-的原理與風險)
- [SCAN 的原理](#scan-的原理)
- [反向二進位迭代:為什麼要這樣走訪](#反向二進位迭代為什麼要這樣走訪)
- [SCAN 的保證與副作用](#scan-的保證與副作用)
- [count 與 MATCH 的真正意義](#count-與-match-的真正意義)
- [glob pattern 語法](#glob-pattern-語法)
- [KEYS 與 SCAN 是不是同一種掃描](#keys-與-scan-是不是同一種掃描)
- [常見誤解:key 是有序的嗎](#常見誤解key-是有序的嗎)
- [使用建議](#使用建議)
- [對照總表](#對照總表)

---

## 前置知識:keyspace 是一張 hash table

Redis 整個資料庫的 key 集合(keyspace)底層就是一張大的 **hash table**(原始碼裡叫 `dict`)。所有 key 依 hash 值分佈到各個 bucket:

```
dict
├── table[0] → key3
├── table[1] → key1 → key7   # 同 bucket 用鏈結串列處理碰撞
├── table[2] → (空)
├── table[3] → key2
└── ... 共 size 個 bucket(size 一定是 2 的次方)
```

兩個關鍵前提:

- **key 是被 hash 打散的,無序**。`user:1`、`user:2` 這種相鄰的 key,hash 後散落在不相干的 bucket。
- **Redis 是單執行緒**。所有指令排隊執行,一次只跑一個 —— 這是理解「堵塞」的根本。

---

## KEYS 的原理與風險

```
KEYS user:*
```

`KEYS` 會**從頭到尾走訪整張 hash table 的每個 bucket、每個 key**,逐一做 pattern 比對,符合的一次收集回傳。

- 複雜度 **O(N)**,N = 資料庫總 key 數。
- 因為單執行緒,掃描期間**所有其他指令全部被凍結**:

```
時間軸(單執行緒):
  Client A: KEYS *   ────[掃描 100 萬 key,耗時 800ms]────►
  Client B: GET foo             ⏳ 卡住等待...
  Client C: SET bar 1           ⏳ 卡住等待...
```

> KEYS 不是「查詢慢」的問題,而是「查詢期間全服務停擺」的問題。百萬級 key 下等同一次小型當機,**正式環境嚴禁使用**。

---

## SCAN 的原理

`SCAN` 解決的問題是:如何在**不一次掃完、不堵塞**的前提下,分多次走完整張表,且**保證不漏**。

用**游標(cursor)** 分段:

```
SCAN 0      → 回傳 (下一個游標=176, 一批 key)
SCAN 176    → 回傳 (下一個游標=232, 一批 key)
SCAN 232    → 回傳 (下一個游標=0,   一批 key)   # 游標回到 0 = 掃完
```

- 游標從 `0` 開始,拿回傳的游標再呼叫,直到游標**再次為 0** 代表一輪結束。
- 每次只處理少量 bucket,**單次呼叫很快、不堵塞**,把大掃描切成很多小步,中間可放行其他指令。

---

## 反向二進位迭代:為什麼要這樣走訪

SCAN 分多次執行、中間放行其他指令,這期間 key 可能增刪,觸發 hash table 的**擴容/縮容(rehash)**,bucket 會重新排列。若游標只是單純 `0,1,2,3...` 遞增,rehash 後會**大量漏掃或重複**。

SCAN 的解法是**反向二進位迭代(reverse binary iteration)** —— 從**最高位**開始進位,而非最低位:

```
假設 size = 8(3 個 bit):
普通遞增:  000 → 001 → 010 → 011 → 100 → 101 → 110 → 111
SCAN 反向: 000 → 100 → 010 → 110 → 001 → 101 → 011 → 111
```

因為 size 一定是 2 的次方(擴容 ×2、縮容 ÷2):

- **擴容**(8→16):原 bucket `x` 的 key 分裂到新表的 `x` 與 `x + 8`。反向迭代從高位往低位走,保證這兩個新 bucket 要嘛都還沒掃、要嘛之後會掃到 —— **不會漏**。
- **縮容**(16→8):兩個舊 bucket 合併,反向迭代保證最多**重複**、不會漏。

---

## SCAN 的保證與副作用

| 保證 | 說明 |
|------|------|
| ✅ 不漏(completeness) | 從游標 0 掃到再次為 0,**全程都存在**的 key 一定至少回傳一次 |
| ⚠️ 可能重複 | 同一 key 可能被回傳多次(尤其 rehash 期間)→ 需自行去重 |
| ❓ 中途增刪不保證 | 掃描期間才新增/刪除的 key,回不回傳都有可能 |
| ⚠️ 每批數量不固定 | 可能回傳**空批**(該次 bucket 剛好都空),**空批不代表結束**,只有游標=0 才是結束 |

對比之下,`KEYS` 是**某一瞬間的精確快照**(不重複、不漏);`SCAN` 是**一段時間內的模糊快照**。這是「分段執行」換來的代價。

---

## count 與 MATCH 的真正意義

```
SCAN 0 MATCH user:* COUNT 100
```

- **COUNT 不是「回我 100 筆」**,而是「這次呼叫大約走訪 100 個 bucket」的**提示**。實際回傳可能遠少於(多數 bucket 空)或多於(某 bucket 多個 key)100 筆。調大 → 往返少但單次久(趨近 KEYS 風險);調小 → 平滑但往返多。100~1000 為常見範圍。
- **MATCH 是取出 bucket 裡的 key「之後」才過濾**。因為符合 pattern 的 key 被 hash 打散在全表各處,Redis **無法跳到「user 區段」**(根本沒有這種區段),只能走訪所有 bucket 再逐一比對。

> 結論:`MATCH` 只減少**回傳給你的結果數**,不減少**底層走訪的 bucket 數**。底層成本仍是 O(N)。MATCH 命中率低時,連續回傳空批是正常現象。

---

## glob pattern 語法

KEYS 與 SCAN 的 pattern 都用 Redis 內建的 glob-style 比對(**不是**正規表達式),逐字元進行:

| 語法 | 意義 | 例 |
|------|------|-----|
| `*` | 任意數量任意字元(含 0 個) | `user:*` |
| `?` | 剛好一個任意字元 | `user:?` |
| `[abc]` | 括號內任一字元 | `user:[12]` |
| `[a-z]` | 範圍 | `key:[a-f]` |
| `[^abc]` | 非括號內字元 | `user:[^0]` |
| `\` | 跳脫特殊字元 | `h\*llo` 比對字面 `h*llo` |

---

## KEYS 與 SCAN 是不是同一種掃描

**工作內容相同,走訪順序不同。**

- 相同:兩者都得走遍整張無序的 hash table、逐 key 比對 pattern,總量都是 O(N)。
- 不同:
  - **KEYS** 一次做完,中途不會 rehash,用最單純的**順序**走訪即可。
  - **SCAN** 分段做、中途放行其他指令、可能遇到 rehash,所以必須用**反向二進位迭代**才能保證不漏,代價是**可能重複、非精確快照**。

心智模型:

```
KEYS = 把房間的燈全開一次看完,但看的期間沒人能進出房間
SCAN = 拿手電筒一格一格照,隨時能讓別人進出,代價是偶爾照到兩次、要照很多次才照完
```

---

## 常見誤解:key 是有序的嗎

**不是。** keyspace 是 hash table,key 被 hash **打散、無序**;hash 的目的是均勻分佈(減少碰撞),不承載原 key 的順序。所以 SCAN **無法靠 hash 值做範圍跳轉**,只能逐 bucket 走訪全表。

真正「有序 + 可範圍掃描」的是 **Sorted Set(ZSet)** —— 那是**單一 key 內部**的結構,與 keyspace 是不同層級:

| | keyspace(key 的集合) | Sorted Set(單一 key 內的值) |
|---|----------------------|------------------------------|
| 底層 | hash table(無序) | **skiplist + hash table** |
| 有序 | ❌ | ✅ 依 score 排序 |
| 範圍查詢 | ❌ 只能 SCAN 全掃 | ✅ 原生支援 |
| 指令 | `SCAN` / `KEYS` | `ZRANGEBYSCORE` / `ZRANGEBYLEX` |

```
# ZSet 的範圍查詢靠底層 skiplist(有序鏈結),可 O(log N) 定位範圍起點
ZRANGEBYSCORE leaderboard 1000 2000   # 依分數範圍
ZRANGEBYLEX   names "[a" "(c"          # 依字典序範圍
```

---

## 使用建議

1. **正式環境一律用 `SCAN`(scan_iter),禁用 `KEYS`。**
2. **需要唯一結果就自行去重**(SCAN 可能回傳重複 key),用 `set` 收集。
3. **未設 `decode_responses=True` 時,回傳是 bytes**,需自行 `.decode()`。
4. **別放進熱路徑。** 即使不堵塞,掃描仍是線性成本。若「經常」需要找出某類 key,代表資料該重新設計 —— 用一個 Redis Set/Hash 當**索引**主動維護,查詢時 O(1):

```python
# ❌ 反模式:每次都掃全庫
keys = await cache.scan_keys("session:user:123:*")

# ✅ 正解:用 Set 當索引
await client.sadd("idx:user:123:sessions", "session:abc")   # 建立時維護
members = await client.smembers("idx:user:123:sessions")     # 查詢 O(1)
```

`SCAN` 的正確定位是**維運、除錯、低頻批次清理**,而非日常查詢介面。

---

## 對照總表

| 面向 | KEYS | SCAN |
|------|------|------|
| 走訪方式 | 一次走完整張表 | 游標式,每次一小段 bucket |
| 走訪順序 | 單純順序 | 反向二進位迭代(容忍 rehash) |
| 複雜度 | O(N) 單次 | 單次 O(count),整輪總計 O(N) |
| 堵塞 | **凍結全服務** | 不堵塞,切成小步 |
| 結果 | 精確快照、不重複 | 可能重複、可能空批、需自行去重 |
| MATCH 效果 | 事後過濾 | 事後過濾(不減少底層走訪量) |
| 適用場景 | 除錯、確定資料極小 | 正式環境唯一正解 |
