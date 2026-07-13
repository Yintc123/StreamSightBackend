# 為什麼 Identity 用兩個獨立 UniqueConstraint 而不是一個

Identity 表定義了**兩個獨立的 UniqueConstraint**、而不是把三欄合併成一個。這篇說明為什麼「三欄合併」看似 DRY 但實際上會漏掉關鍵約束。相關程式碼位於 `app/models/identity.py`。

## 目錄

- [表結構與約束](#表結構與約束)
- [兩個約束對應的兩個業務規則](#兩個約束對應的兩個業務規則)
- [三欄合併為何漏掉兩個 bug](#三欄合併為何漏掉兩個-bug)
  - [SQL 對 NULL 的三值邏輯](#sql-對-null-的三值邏輯)
  - [實測驗證](#實測驗證)
- [為什麼兩個獨立約束剛好對](#為什麼兩個獨立約束剛好對)
- [Postgres 15 的 `NULLS NOT DISTINCT` 也救不了](#postgres-15-的-nulls-not-distinct-也救不了)
- [設計原則:一個 constraint 表達一個業務規則](#設計原則一個-constraint-表達一個業務規則)
- [對照總表](#對照總表)

---

## 表結構與約束

`identities` 表(見 `app/models/identity.py`):

```python
class Identity(Base):
    __tablename__ = "identities"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credential: Mapped[str] = mapped_column(String(255), default="")

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_identity_user_provider"),
        UniqueConstraint("provider", "provider_user_id", name="uq_identity_provider_sub"),
    )
```

**注意兩點**:
1. `provider_user_id` 是 `nullable=True` — 密碼登入的 identity 這個欄位 = NULL、OAuth identity 才有 sub
2. 有**兩個獨立的 UniqueConstraint** — 不是一個三欄合併

---

## 兩個約束對應的兩個業務規則

兩個約束**表達兩個獨立的業務不變量(invariant)**:

### 規則 1:一 user 一 provider 只能綁一次

```python
UniqueConstraint("user_id", "provider", ...)
```

**意義**:
- Alice 不能有兩個 `password` identity(避免密碼混亂)
- Alice 不能綁兩個 `google` identity(一個 Google 帳號綁完再綁一個沒意義)
- 但 Alice 可以同時有 `password` + `google` + `github`(帳號綁定合法)

### 規則 2:一個 OAuth account 只能綁一個 user

```python
UniqueConstraint("provider", "provider_user_id", ...)
```

**意義**:
- **同一個 Google sub 不能同時綁到 Alice 和 Bob**
- 這是 security 關鍵 — 若沒這條、attacker 拿到 Alice 的 Google account 就能把它綁到自己帳號、達成 account hijack
- 業界所有 OAuth 系統都有這條約束

---

## 三欄合併為何漏掉兩個 bug

**看似 DRY 的錯誤設計**:

```python
# ❌ 三欄合併
UniqueConstraint("user_id", "provider", "provider_user_id", name="uq_all_three")
```

這條約束**兩個 bug 都擋不到**。

### SQL 對 NULL 的三值邏輯

**SQL 標準**:`NULL != NULL`(NULL 代表「未知」、兩個未知不能斷定相等)。

**Postgres UniqueConstraint 預設行為**:**兩個 NULL 視為 distinct、不會擋**。

**這對三欄合併是致命的**、因為 password identity 的 `provider_user_id = NULL`。

#### Bug 1:Alice 意外有兩個 password identity(該擋卻沒擋)

| Row | tuple | 三欄合併是否擋 |
|---|---|---|
| A | `(1, "password", NULL)` | ✅ 插入 |
| B | `(1, "password", NULL)` | ❌ **也允許**(NULL != NULL) |

**結果**:Alice 有兩個 password identity、系統邏輯要嘛拿 A 拿 B 隨機、要嘛需要「取最新」的複雜處理 — 但這問題根本不應該發生。

#### Bug 2:兩個 user 綁同一個 Google account(該擋卻沒擋)

| Row | tuple | 三欄合併是否擋 |
|---|---|---|
| A | `(1, "google", "sub-shared")` — Alice | ✅ 插入 |
| B | `(2, "google", "sub-shared")` — Bob | ❌ **也允許**(第一欄 user_id 不同 → tuple 不同) |

**結果**:attacker 拿到 Alice 的 Google account、能綁到自己 user id、下次 Alice 用 Google 登入時看到的是 attacker 的資料 — **完美的 account hijack**。

### 實測驗證

用 Postgres 實測驗證(見 `docs/` 這個檔案的 commit context):

```
=== 三欄合併: UNIQUE (user_id, provider, provider_user_id) ===
  [Bug 1] ❌ 未擋 — Alice 有 2 個 password identity
  [Bug 2] ❌ 未擋 — Bob 綁了 Alice 的 Google 帳號

=== 兩個獨立: (user_id, provider) + (provider, provider_user_id) ===
  [Bug 1] ✅ 已擋 — IntegrityError
  [Bug 2] ✅ 已擋 — IntegrityError
```

**兩個 bug 都是「兩個獨立約束擋、三欄合併漏」**。

---

## 為什麼兩個獨立約束剛好對

**兩個約束分別處理不同 NULL 語意的場景**:

### 約束 1:`(user_id, provider)` — 兩欄都非 NULL、乾淨執行

Password 和 OAuth identity 的 `user_id + provider` 都有值(never NULL)。**同 user + 同 provider = tuple 一定 duplicate → 擋**。

### 約束 2:`(provider, provider_user_id)` — 只對 OAuth 起作用

**OAuth 情境**:`provider + sub` 都有值、正常 unique → 擋 account hijack。

**Password 情境**:`provider_user_id = NULL` → 這個約束對 password rows 「不生效」(NULL 讓 tuple 不 distinct 判斷) → **這是正確行為、不需要它擋**(規則 1 已經擋 Alice 的重複 password)。

### 分工圖

| 情境 | 誰擋 |
|---|---|
| Alice 建兩個 password | 約束 1 (`user_id + provider`) |
| Alice 建兩個 google | 約束 1 |
| Alice 綁 password + google + github(帳號綁定) | 都允許(不同 provider) |
| Bob 想綁 Alice 的 Google sub | 約束 2 (`provider + provider_user_id`) |

**每個攻擊向量都有對應約束擋、沒有交集也沒有漏洞**。

---

## Postgres 15 的 `NULLS NOT DISTINCT` 也救不了

Postgres 15+ 引入新語法:

```sql
UNIQUE NULLS NOT DISTINCT (user_id, provider, provider_user_id)
```

**改變 NULL 語意**:兩個 NULL 視為相等、會擋。

**用這個救三欄合併?**

**部分能救 Bug 1**:Alice 兩個 password 會擋(兩個 NULL 現在算相等)。

**Bug 2 還是漏**:第一欄 `user_id` 就 distinct、`NULLS NOT DISTINCT` 幫不上忙(這 case 根本沒 NULL、是欄位差異)。

### 其他缺點

- **Postgres-only** — SQLite / MySQL / MariaDB 都不支援、template 失去移植性
- **語意仍不對** — 三欄合併表達的是「這三欄的組合不重複」、不是「provider + sub 單獨不重複」
- 就算 hack 成能擋、也是**逆向工程**、不符合「schema 直接反映業務規則」的原則

---

## 設計原則:一個 constraint 表達一個業務規則

**Database schema 的設計哲學**:**約束應該直接映射業務不變量**、一對一。

**兩個獨立約束**:
- `(user_id, provider)` = 「一 user 一 provider 唯一」
- `(provider, provider_user_id)` = 「一個 OAuth 帳號綁一個 user」

**讀 schema 的人一眼看懂**這兩條業務規則是什麼。

**三欄合併**:
- 表達不出上述任何一條
- 讀 schema 的人要腦補「這個 constraint 想幹嘛」
- Debug 時得反推「這 error 是哪個規則被違反」

**教科書級的 database design 準則**:
> Each constraint should express exactly one business invariant.

「DRY」原則不應該套用到 constraint 上 — 每個 constraint 有獨立語意、合併只是**視覺上短、實際上意義損失**。

---

## 對照總表

| 面向 | 三欄合併 UNIQUE(user_id, provider, provider_user_id) | 兩個獨立 UNIQUE(user_id, provider) + UNIQUE(provider, provider_user_id) |
|---|---|---|
| **擋 Alice 兩個 password** | ❌(NULL != NULL) | ✅(約束 1) |
| **擋 OAuth account hijack** | ❌(user_id 不同就 distinct) | ✅(約束 2) |
| **業務規則映射** | 一對零、不明確 | 一對一、乾淨 |
| **Portable** | Postgres 15+ 才能靠 NULLS NOT DISTINCT 部分修正 | 標準 SQL、跨 DB 都支援 |
| **讀 schema 的可讀性** | 「這 constraint 幹嘛的?」 | 「這條擋 X、那條擋 Y」一目了然 |
| **Debug integrity error** | 難確認是哪條規則被違反 | 錯誤名字直接告訴你 |
| **教科書級?** | ❌ 反 pattern | ✅ 標準做法 |

**一句話**:**每個 UniqueConstraint 對應一個獨立的業務規則、合併只是視覺上的 DRY、實際上損失語意 + 引入 NULL bug**。這個 template 的 `identities` 表寫兩個約束是正確的、不是冗餘。
