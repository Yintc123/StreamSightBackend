# Salt 與 IV:兩個看似相似其實完全不同的機制

Salt 和 IV 都是「附加給密碼學運算的一段資料」,但**解決的問題完全不同**。這篇說明本專案何時用固定 IV、何時用隨機 salt、以及為什麼。相關程式碼位於 `app/core/db/types.py`(email 加密)和 `app/core/auth/password.py`(密碼 hash)。

## 目錄

- [先分清:加密 vs hash](#先分清加密-vs-hash)
- [IV 是什麼](#iv-是什麼)
- [Salt 是什麼](#salt-是什麼)
- [本專案的兩個實例](#本專案的兩個實例)
  - [Email:固定 IV 的 deterministic encryption](#email固定-iv-的-deterministic-encryption)
  - [Password:隨機 salt 的 argon2 hash](#password隨機-salt-的-argon2-hash)
- [決策準則](#決策準則)
- [常見誤解](#常見誤解)
- [對照總表](#對照總表)

---

## 先分清:加密 vs hash

Salt 用在 hash、IV 用在加密。兩者的本質不同:

| 面向 | 加密 (encryption) | Hash |
|---|---|---|
| **目的** | 保護資料、需要時能還原成明文 | 驗證資料、**永遠不需還原** |
| **可逆嗎** | 可逆(有 key 就能還原) | 不可逆(單向函式) |
| **輸入相同 → 輸出相同?** | 看設計(deterministic 或 randomized) | 看是否加 salt |
| **典型演算法** | AES / ChaCha20 | SHA-256 / argon2 / bcrypt |
| **本專案例子** | Email column 加密 | Password 儲存 |

**先記住這條規則**:

- 需要**還原明文**(如 email 查詢) → 用加密 + IV
- 只需**驗證是否相符**(如密碼登入) → 用 hash + salt

---

## IV 是什麼

**IV = Initialization Vector,初始化向量**。用在**對稱加密的 block cipher mode**(如 AES-CBC、AES-CTR)。

### IV 的角色

以 AES-CBC(本專案 email 加密用的模式)為例:

```
plaintext:   [block 1] [block 2] [block 3] ...
                 ↓         ↓         ↓
             XOR IV     XOR C1    XOR C2
                 ↓         ↓         ↓
              AES()     AES()      AES()
                 ↓         ↓         ↓
ciphertext:  [C1]      [C2]       [C3]
```

- 第一個 block 用 IV 做 XOR、之後每個 block 用前一個 ciphertext 做 XOR
- **IV 的作用**:讓「第一個 block」有東西可 XOR、避免相同 plaintext 的開頭產生相同 ciphertext

### IV 的兩種用法

| 用法 | 效果 | 適用 |
|---|---|---|
| **隨機 IV** | 同 plaintext 每次加密結果不同(non-deterministic) | 一般加密、防頻率分析 |
| **固定 IV** | 同 plaintext 每次加密結果相同(deterministic) | 需要 SQL 查詢/unique 索引的欄位 |

### 為什麼固定 IV 不夠安全但本專案還是用了

**固定 IV 的問題**:同一個明文永遠加出同一個密文,attacker 可以做頻率分析(看哪兩筆是同一 email)。

**本專案的取捨**:
- 若用隨機 IV:每次 encrypt("alice@example.com") 得到不同密文 → 無法用 `WHERE email = ...` 查詢、無法用 UNIQUE 索引
- 若用固定 IV:能查詢、能 UNIQUE、代價是頻率分析漏洞
- **選固定 IV** — 因為 DB admin 偷看能被擋(見 mask 不到明文)、頻率分析在此場景可接受

見 `app/core/db/types.py`:

```python
_FIXED_IV: bytes = b"\x00" * 16
```

---

## Salt 是什麼

**Salt = 加進密碼裡一起 hash 的隨機字串**。目的**不是機密**、而是**讓每個用戶的 hash 都不同**。

### Salt 的角色

沒 salt 時:

```
"password123" → hash → 482c811da5d5b4bc6d497ffa98491e38
                          ↑
                     attacker 拿到 DB dump,對照彩虹表 → 秒破
```

有 salt 時:

```
"password123" + salt_A ("aXNP...") → hash → HASH_A
"password123" + salt_B ("bYOQ...") → hash → HASH_B
                                            ↑
                                     兩個 hash 完全不同、彩虹表沒用
```

### Salt 為什麼公開也安全

**Salt 存在 hash 字串裡、跟 hash 一起儲存進 DB。DB 洩漏時 attacker 拿得到 salt**。這是設計、不是漏洞。

Argon2 hash 字串結構:

```
$argon2id$v=19$m=65536,t=3,p=4$aXNPjF9x6cRlA/QK3wYw6A$L9vT+Kx8N7Rq...
     ↑       ↑    ↑                    ↑                     ↑
   算法    版本  參數                隨機 salt 22 chars       hash 43 chars
```

Salt 公開但**強度來自「每個 user 的 salt 都不同」+「argon2 本身故意設計得慢(每 hash ~50ms)」**:

- Attacker 想破 1 個 user → 對這個 user 的 salt 建 rainbow table → argon2 慢、預算好一個 table 要幾天
- Attacker 想破全 DB → 每 user 都要重建 table → 破整個 DB 要幾年

**Salt 目的:讓 attacker 沒辦法「一組 rainbow table 破全 DB」**。

### Verify 時怎麼用 salt

`argon2` 的 `verify(hashed, plain)` 內部:

1. 從 `hashed` 字串裡**解析出 salt**
2. 用同 salt + params + 用戶輸入的 plain 算一次新 hash
3. 比對算出的新 hash 是否 == `hashed` 裡的舊 hash
4. 相等 → 密碼對;不等 → 密碼錯

見 `app/core/auth/password.py`:

```python
def verify_password(plain: str, hashed: str) -> bool:
    try:
        _password_hasher.verify(hashed, plain)  # 內部自動抽 salt 重算
        return True
    except VerifyMismatchError:
        return False
```

**你不用手動管 salt**、argon2 幫你藏在 hash 字串裡自動搞定。

---

## 本專案的兩個實例

### Email:固定 IV 的 deterministic encryption

**位置**:`app/core/db/types.py::DeterministicEncryptedString`

**演算法**:AES-256-CBC + 固定 IV(全 0)+ PKCS7 padding + hex encoding

**為什麼需要 deterministic**:

- Business 需求:`WHERE email = 'alice@example.com'` 要能查得到
- Business 需求:`users.email` 要能 UNIQUE 防重複註冊
- 兩者都需要「同 email → 同 ciphertext」

**取捨**:

| 得到 | 失去 |
|---|---|
| DB 查詢能 work | 頻率分析(attacker 能看出「這兩筆是同 email」) |
| UNIQUE 索引能 work | 不能存 binary 或需極強機密 |
| 索引 lookup 快 | |

**流程**:

```python
# 存入
plaintext = "alice@example.com"
padded = pkcs7_pad(plaintext.encode())        # 補齊 16 bytes 倍數
ciphertext = AES_CBC_encrypt(padded, KEY, IV=b"\x00"*16)
db_value = ciphertext.hex()                     # 存 hex 字串進 DB

# 查詢
query_email = "alice@example.com"
query_ciphertext_hex = encrypt(query_email).hex()  # 用同 KEY + 同 IV
# → 得到跟 DB 內同一個 hex 字串 → WHERE email = ? 命中
```

**適用場景**:需要**支援 SQL 查詢**的敏感欄位(email、phone、tax_id 等)。**不適用**於高度機密的欄位(password、SSN 等)。

### Password:隨機 salt 的 argon2 hash

**位置**:`app/core/auth/password.py`

**演算法**:argon2id(argon2 的 hybrid variant、抗 side-channel 和 GPU brute force)

**為什麼需要隨機 salt**:

- 密碼**永遠不需要「還原」**、只需要驗證「用戶輸入的密碼是否對」
- DB 洩漏後、必須**確保 attacker 沒辦法用彩虹表反推**
- 每 user salt 不同 → attacker 要對每 user 各建一次 table → 慢到無法規模化攻擊

**流程**:

```python
# 存入
plaintext = "user_password_123"
salt = random_bytes(16)                          # 每次 hash 產新 salt
hash_result = argon2(plaintext, salt, params)
db_value = f"$argon2id$v=19$m=65536,t=3,p=4${b64(salt)}${b64(hash_result)}"

# 驗證(login)
input_password = "user_password_123"
stored_hash = load_from_db()
salt = parse_salt_from(stored_hash)             # argon2 幫你自動解析
recomputed = argon2(input_password, salt, params)
match = recomputed == parse_hash_from(stored_hash)
```

**適用場景**:任何**只需驗證、不需還原**的敏感資料(password 是唯一實務例子、其他很罕見)。

---

## 決策準則

**問三個問題**:

1. **你需要還原明文嗎?**
   - 是 → 用加密(需要 IV 或 nonce)
   - 否 → 用 hash(需要 salt)

2. **(若需要還原)你需要 SQL 查詢/UNIQUE 這個欄位嗎?**
   - 是 → **固定 IV**(deterministic encryption)、犧牲頻率分析安全性
   - 否 → **隨機 IV**(non-deterministic encryption)、標準加密方式

3. **(若不需還原)這個欄位需要抗彩虹表嗎?**
   - 是 → 用 **argon2 / bcrypt / scrypt** + 自動隨機 salt
   - 否 → 用 SHA-256(但幾乎所有真實敏感資料都需要抗彩虹表、直接用 argon2 更省事)

---

## 常見誤解

### 誤解 1:「password 也用 deterministic encryption 存,登入時 encrypt 密碼比對」

**大錯特錯**。

- Deterministic encryption 的 key 是固定的 → key 洩漏 = 所有密碼曝光
- 用戶 A 密碼 = "123456" → encrypt 出的密文,任何其他用戶密碼 = "123456" 也會加出同樣密文 → attacker 可分析
- **Password 一律用 hash + 隨機 salt**、絕不用可逆加密

### 誤解 2:「salt 也要保密、不能存 DB」

**salt 公開沒關係**。Salt 的作用是「讓每 user 的 hash 不同」防彩虹表、不是機密。**藏 salt 反而製造麻煩**(verify 時要去別的地方查 salt、DB 洩漏 salt 也一起洩、藏也白藏)。

### 誤解 3:「固定 IV 跟沒用 IV 一樣、加密不安全」

**加密仍安全**(明文還是被 AES 保護、attacker 沒 key 破不了)、但**「同 plaintext → 同 ciphertext」讓 attacker 能做頻率分析**。這是**取捨**、不是漏洞:本專案為了 SQL 查詢需求選擇這個取捨。

### 誤解 4:「argon2 兩次 hash 同密碼結果不同、那怎麼驗證?」

**salt 藏在 hash 字串裡**。Verify 時 argon2 從 hashed 字串裡解析出當初用的 salt、用同 salt 重算、比對就對得上。**你完全不用管 salt**。

### 誤解 5:「用 md5 + salt 就夠了、argon2 太慢」

**md5 太快**是弱點不是優點。Attacker 用 GPU 一秒能算幾十億次 md5、有 salt 也擋不住(對每 user salt 也很快)。**argon2 故意設計得慢**(每 hash ~50ms、故意消耗記憶體),attacker 破 1 個 user 就要幾天、破全 DB 幾年。**慢 = 安全**。

---

## 對照總表

| 面向 | Email 加密 | Password hash |
|---|---|---|
| **演算法** | AES-256-CBC | argon2id |
| **附加資料** | IV(初始化向量) | Salt(鹽) |
| **附加資料值** | 固定(全 0)、hardcoded | 隨機、每次不同 |
| **附加資料存哪** | code 裡(`_FIXED_IV`) | 存進 hash 字串本身 |
| **可逆嗎** | 可以(有 key) | 不可以 |
| **同輸入 → 同輸出?** | 是(deterministic) | 否(每次不同) |
| **同 salt/IV 洩漏會怎樣** | 加密仍安全(靠 key)、但可頻率分析 | 無感、salt 本來就公開 |
| **本專案位置** | `app/core/db/types.py::DeterministicEncryptedString` | `app/core/auth/password.py::hash_password` |
| **驅動需求** | SQL 查詢 + UNIQUE 索引 | 防彩虹表 + 抗 GPU brute force |
| **絕不能反向用** | 拿它存密碼(可被反推) | 拿它存 email(每次不同、無法查詢) |

**一句話總結**:**加密用 IV(可能固定可能隨機、看是否需要查詢)、Hash 用 salt(永遠隨機、且公開)**。混用是災難。
