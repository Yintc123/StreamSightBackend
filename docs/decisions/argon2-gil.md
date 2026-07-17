# Argon2 與 GIL:為什麼「刻意很慢」的雜湊不會拖垮整個服務

密碼雜湊有個違反直覺的設計目標:**它「刻意」很慢**。Argon2 一次 hash 可能要花幾十到幾百毫秒——這在 CPU 運算的世界裡是天文數字。問題來了:Python 有 GIL,同時只能跑一條 thread 的 bytecode,那這種「吃滿 CPU 又很慢」的運算,不是應該把整個 event loop 卡死嗎?

答案的關鍵,是 Argon2 的 C 實作在運算時會**主動釋放 GIL**。這篇說明這件事為什麼重要、以及它對本專案 `app/core/auth/password.py` 的實務影響。

## 目錄

- [先講重點](#先講重點)
- [為什麼密碼雜湊要「刻意很慢」](#為什麼密碼雜湊要刻意很慢)
- [那不就跟 GIL 衝突了嗎?](#那不就跟-gil-衝突了嗎)
- [Argon2 的殺手鐧:運算時釋放 GIL](#argon2-的殺手鐧運算時釋放-gil)
- [釋放 GIL 帶來的兩個實際好處](#釋放-gil-帶來的兩個實際好處)
- [但光「會釋放」還不夠:必須配合 offload](#但光會釋放還不夠必須配合-offload)
- [本專案現況](#本專案現況)
- [對照:哪些運算會釋放-GIL](#對照哪些運算會釋放-gil)
- [常見誤解](#常見誤解)
- [總結](#總結)

---

## 先講重點

1. 密碼雜湊(Argon2)是 **CPU bound** 而且**刻意很慢**——這是安全需求,不是缺點。
2. 純 `async` / `await` **無法**解決 CPU bound;這點不要搞錯。
3. Argon2 真正的優勢:它的核心運算是 **C 寫的**,在跑重運算時會呼叫 `Py_BEGIN_ALLOW_THREADS` **主動釋放 GIL**。
4. 因此只要把 hash **offload 到另一條 thread**,那條 thread 能與 event loop **真正平行**跑,不會互搶 GIL,整個服務不被凍結。
5. 條件是「**offload 到別的 thread**」+「**Argon2 會放 GIL**」兩者同時成立,缺一不可。

---

## 為什麼密碼雜湊要「刻意很慢」

一般演算法追求「越快越好」,密碼雜湊反過來——**越慢越安全**(在可接受範圍內)。原因是防禦「離線暴力破解」:

假設資料庫外洩,攻擊者拿到一堆雜湊值,想反推原始密碼,唯一辦法是「猜一個密碼 → 算 hash → 比對」不斷重試。

| 雜湊速度 | 攻擊者每秒能猜幾次 |
|---|---|
| SHA-256(快,約 1μs) | 每張顯卡**數十億次/秒** → 弱密碼幾秒就破 |
| Argon2(慢,約 100ms) | 每次都要 100ms + 大量記憶體 → 破解成本高數百萬倍 |

Argon2 還額外吃**大量記憶體**(memory-hard),讓攻擊者無法用便宜的 GPU/ASIC 大規模平行猜測。這就是它被選為 [Password Hashing Competition](https://www.password-hashing.net/) 冠軍、也是本專案採用 `argon2id` 的原因。

**所以「慢」是 feature 不是 bug。** 但這個 feature 直接製造了一個工程難題:一個刻意花 100ms 的純 CPU 運算,放進單執行緒的 event loop 會怎樣?

---

## 那不就跟 GIL 衝突了嗎?

你的直覺是對的。回顧兩個事實:

- **GIL**:CPython 同時只允許**一條 thread** 執行 Python bytecode。
- **event loop**:asyncio 的併發靠「單一 thread 內、在 `await` 點切換 coroutine」。切換是**協作式**的——只有碰到會掛起的 `await` 才讓出控制權。

把這兩點套到密碼雜湊上,如果**直接**在 event loop 上算:

```python
@router.post("/register")
async def register(...):
    password_hash = hash_password(payload.password)  # ❌ 100ms 純 CPU
    # 這 100ms 內沒有 await、也沒有別的 thread 幫忙
    # → event loop 被凍結,期間所有其他 request 全部塞車
```

這段運算不會碰到 `await`,event loop 完全沒機會切走。**光靠 async 語法救不了它**——這正是你該質疑「用 async 解決雜湊成本」這種說法的地方。

---

## Argon2 的殺手鐧:運算時釋放 GIL

轉機在於:GIL **不是一直被鎖住的,它可以被「放開」**。

`argon2-cffi`(本專案用的套件)的核心雜湊運算是用 **C 寫的**,不是 Python bytecode。C 擴充在進入一段「純運算、完全不碰 Python 物件」的區塊前,可以主動歸還 GIL:

```c
Py_BEGIN_ALLOW_THREADS       // 「接下來是純運算,GIL 還你,我不需要」
    ... 幾十~幾百 ms 的 Argon2 運算(純 C + 記憶體操作)...
Py_END_ALLOW_THREADS         // 「算完了,把 GIL 拿回來」
```

**在這兩行之間,GIL 是放開的。** 此時如果有另一條 thread 想跑 Python,它可以立刻拿到 GIL 執行。

這就打破了「GIL 讓同時只能跑一條」的直覺。更精確的說法是:

> GIL 限制的是同時執行 **Python bytecode** 的 thread 數;而釋放了 GIL 的 **C 運算不算 Python bytecode**,可以與另一條跑 Python 的 thread **真正平行**。

```
Worker Thread:  跑 Argon2 的 C 運算(已釋放 GIL,吃滿一個 CPU core)
Main Thread:    event loop 繼續服務其他 request(拿著 GIL 跑 Python)
        ↑ 這兩條「真的同時」在跑,因為 Argon2 那條不需要 GIL
```

---

## 釋放 GIL 帶來的兩個實際好處

**好處一:不凍結 event loop。** 把 hash 丟到 worker thread 後,主執行緒(event loop)因為 Argon2 放了 GIL 而能繼續拿著 GIL 跑,持續服務別的 request。單一使用者註冊要等的 100ms,不會變成「全站卡 100ms」。

**好處二:多核真正平行。** 十個使用者同時註冊,十個 Argon2 運算在 thread pool 裡,因為每個都放 GIL,可以**分散到多個 CPU core 真正平行跑**。對比之下,純 Python 的 CPU 運算(不放 GIL)開再多 thread 也只能輪流用一顆核心。

這第二點是 Argon2(以及 bcrypt、大多數加密 C 函式庫)相對「純 Python CPU 工作」的根本優勢:**它讓 thread pool 這個手段真的有效**,而不是被 GIL 綁死。

---

## 但光「會釋放」還不夠:必須配合 offload

這是最容易誤會的地方。**「Argon2 會釋放 GIL」不代表「直接呼叫就沒事」。**

```python
async def register(...):
    hash = hash_password(pw)          # ❌ 在主執行緒上算
```

即使 Argon2 內部放了 GIL,你若在**主執行緒**直接呼叫它,主執行緒本身還是卡在那個 C 呼叫裡出不來。放 GIL 只是「允許**別的** thread 來跑」,但你根本沒開別的 thread,等於白放。event loop 一樣凍結。

正確做法是**主動把它推到另一條 thread**,主執行緒才空得出來:

```python
import asyncio

async def register(...):
    hash = await asyncio.to_thread(hash_password, pw)
    #      ↑ 主執行緒在此 await → 控制權交還 event loop
    #        event loop 趁這段去服務別的 request
    #        hash_password 在 worker thread 上跑,且已釋放 GIL → 真正平行
```

兩個條件在這裡才**同時**成立:

1. `await asyncio.to_thread(...)` → 主執行緒讓出控制權(async 的功勞)
2. Argon2 在 worker thread 上、且釋放 GIL → 真正平行、不搶主執行緒 GIL(C 實作的功勞)

**FastAPI 的補充機制:** 若把 router 寫成**同步 `def`**(而非 `async def`),FastAPI 會自動把整個 handler 丟到 thread pool 執行。因此「同步 `def` + 直接呼叫 `hash_password`」是安全的;反而「`async def` + 直接呼叫」才會凍結 loop。**`async def` 要更小心**,因為它保證跑在 event loop 上,任何阻塞都直接砸中 loop。

---

## 本專案現況

> **已更新（現況已 offload）**:本節早期描述「本專案尚未 offload」已**過時**。`app/core/auth/password.py` 現已把 hash / verify 都包成 **`async` + `asyncio.to_thread`**,`app/services/auth.py` 呼叫端也全數 `await`。以下為現行實作。

`app/core/auth/password.py` 已把封裝改為 async、內部 offload 到 threadpool:

```python
import asyncio

_password_hasher: PasswordHasher = PasswordHasher()

async def hash_password(plain: str) -> str:
    """Hash a plaintext password using argon2id (offloaded to threadpool)."""
    return await asyncio.to_thread(_password_hasher.hash, plain)

async def verify_password(plain: str, hashed: str) -> bool:
    def _verify() -> bool:
        try:
            _password_hasher.verify(hashed, plain)
            return True
        except VerifyMismatchError:
            return False
    return await asyncio.to_thread(_verify)
```

`app/services/auth.py` 的呼叫端已全數 `await`(offload 生效):

```python
async def register(self, payload: RegisterRequest) -> TokenPayload:
    password_hash: str = await hash_password(payload.password)   # ✅ offload 到 worker thread
    ...

async def login(self, payload: LoginRequest) -> TokenPayload:
    if identity is None or not await verify_password(payload.password, identity.credential):
        ...                                                       # ✅ 同樣 offload
```

**現況評估:**
- 「offload 到 thread」+「Argon2 放 GIL」兩條件**皆已成立**——單一請求的雜湊延遲只會讓「該請求自己等」,不再凍結 event loop、高併發下多核可真正平行。
- 密碼登入的 hash 存於 `identities.credential`(password provider),非 `user.password_hash`;**CMS admin**(見 [`jwt-role-and-admin.md`](./jwt-role-and-admin.md))則自帶 `admins.password_hash`,登入時**複用同一組 `verify_password`**,自動享有 offload,無需另行處理。

---

## 對照:哪些運算會釋放 GIL

| 工作類型 | 會釋放 GIL? | thread pool 有效? | 說明 |
|---|:---:|:---:|---|
| I/O(DB、網路、檔案) | ✅ | ✅ | 等待時由底層 C 釋放 GIL |
| Argon2 / bcrypt(C 擴充) | ✅ | ✅ | 運算時**主動**釋放 GIL,可多核平行 |
| numpy 大量運算 | ✅(多數) | ✅ | 底層 C/BLAS 常會釋放 |
| 純 Python CPU 迴圈 | ❌ | ❌ | 一直持有 GIL,需改用**多進程**繞過 |

重點:**thread pool 能不能幫上 CPU bound,取決於那段運算「會不會釋放 GIL」。** Argon2 會,所以 thread pool 對它有效;純 Python 運算不會,只能靠 `ProcessPoolExecutor`(多進程)真正平行。

---

## 常見誤解

**「用 async 就能解決 Argon2 的 CPU 成本。」**
❌ async 本身解決不了任何 CPU bound。它的功勞只是「讓主執行緒在等 hash 時去做別的事」,真正繞過 GIL 靠的是 Argon2 的 C 實作放 GIL。兩者要配合。

**「Argon2 會放 GIL,所以直接呼叫就不會卡 loop。」**
❌ 放 GIL 只允許「別的 thread」來跑。若你在主執行緒直接呼叫、沒開別的 thread,主執行緒還是卡死。必須 offload。

**「GIL 讓 Python 完全不能平行,多開 thread 沒意義。」**
❌ 對「純 Python CPU 運算」成立;但對「會釋放 GIL 的 C 擴充(I/O、Argon2、numpy)」不成立——這些場景多 thread 能真正平行。

**「密碼雜湊太慢,應該換快一點的演算法。」**
❌ 慢是**安全需求**。正確方向是「保持慢 + offload 到 thread」,而不是犧牲安全去換快。

---

## 總結

- 密碼雜湊**刻意很慢**是為了擋離線暴力破解——這是 feature。
- 慢 + CPU bound 本來會凍結單執行緒的 event loop,而 Python 又有 GIL。
- **Argon2 的核心優勢**:C 實作在運算時**主動釋放 GIL**,使其能與 event loop **真正平行**、也能在 thread pool 裡**多核平行**。
- 但必須**主動 offload 到別的 thread**(`asyncio.to_thread` 或同步 `def` handler 讓 FastAPI 代勞),釋放 GIL 才有意義。
- 「offload 到 thread」+「Argon2 放 GIL」兩者缺一不可。

相關程式碼:`app/core/auth/password.py`(hash 封裝)、`app/services/auth.py`(呼叫點)。密碼為何用隨機 salt 見 [salt-and-iv.md](./salt-and-iv.md)。
