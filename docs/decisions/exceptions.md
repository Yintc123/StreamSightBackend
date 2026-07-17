# 例外處理機制 (Exception Handling)

本專案的業務層例外與 FastAPI handler 的運作方式。程式碼位於 `app/core/exceptions/`。

## 目錄

- [設計概觀](#設計概觀)
- [檔案結構](#檔案結構)
- [AppException:業務層例外基底](#appexception業務層例外基底)
- [為什麼 message 要存兩份](#為什麼-message-要存兩份)
- [handler 是怎麼接住例外的](#handler-是怎麼接住例外的)
- [handler 註冊順序與比對規則](#handler-註冊順序與比對規則)
- [使用方式](#使用方式)
- [回應格式](#回應格式)
- [與其他框架的比較](#與其他框架的比較)

---

## 設計概觀

分兩層:

- **業務層 (business layer)**:`AppException` 及其子類別。程式碼裡 `raise NotFoundError(...)` 用的是這些。
- **框架層 (framework layer)**:Starlette/FastAPI 的 `HTTPException`、`RequestValidationError`。

所有例外最終都被統一轉成**標準化的 JSON 錯誤回應**(帶 `request_id`),由 `handlers.py` 負責。

---

## 檔案結構

```
app/core/exceptions/
├── __init__.py     # 對外匯出 AppException、各子類別、setup_exception_handlers
├── base.py         # AppException 基底 + 各業務子類別
└── handlers.py     # 各 handler function + setup_exception_handlers()
```

---

## AppException:業務層例外基底

```python
class AppException(Exception):
    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str = "", *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message: str = message
        self.details: dict = details or {}
```

子類別只覆寫兩個**類別屬性 (class attribute)**:

| 子類別 | status_code | error_code |
|--------|-------------|------------|
| `NotFoundError` | 404 | `not_found` |
| `UnauthorizedError` | 401 | `unauthorized` |
| `ForbiddenError` | 403 | `forbidden` |
| `ConflictError` | 409 | `conflict` |
| `BadRequestError` | 400 | `bad_request` |
| `BusinessRuleError` | 422 | `business_rule_violation` |

### class 屬性 vs 實例屬性

- `status_code` / `error_code` 寫在 class body → **所有實例共享一份**,因為它們是固定值。
- `message` / `details` 寫在 `__init__` 的 `self.xxx` → **每個實例各自獨立**,因為每次 raise 都不同。

> 注意:`base.py` 定義的是 **class(模具),不是實例**。每次 `raise NotFoundError(...)` 都會建立一個**全新的實例**,彼此獨立,不是 singleton。

### `*` 強制關鍵字參數

`__init__` 簽章中的 `*` 強制 `details` 一定要用關鍵字傳入,避免位置參數傳錯:

```python
raise NotFoundError("user", {"id": 1})           # ✗ TypeError
raise NotFoundError("user", details={"id": 1})   # ✓
```

---

## 為什麼 message 要存兩份

`__init__` 把 message 存到**同一個物件的兩個不同欄位**,用途不同:

| 這行 | 存到 | 誰會讀 |
|------|------|--------|
| `super().__init__(message)` | `self.args` | Python 內建:`str(e)`、traceback、logging |
| `self.message = message` | `self.message` | 我們自己的 handler(讀 `exc.message`) |

如果**只**寫 `self.message` 而不呼叫 `super().__init__()`,`self.args` 會是空的,traceback / log 就印不出訊息:

```python
e = NotFoundError("user not found")
print(str(e))       # 'user not found'  ← 來自 super().__init__
print(e.message)    # 'user not found'  ← 來自 self.message
```

這兩份各自獨立,不會互相同步。

---

## handler 是怎麼接住例外的

核心觀念:**Starlette 用「繼承關係 (isinstance / MRO)」比對,不是用型別名字完全相等。** 所以只註冊父類別 `AppException`,所有子類別都會被接住。

`setup_exception_handlers()` 註冊:

```python
def setup_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
```

當 `raise NotFoundError("...")` 冒泡到 Starlette,它會沿著例外的 **MRO(繼承鏈)** 由子往父找第一個註冊過的 handler:

```
NotFoundError → AppException → Exception → BaseException → object
                    ↑
              在這一層命中 → app_exception_handler
```

| 順序 | dict 裡有註冊嗎 | 結果 |
|------|-----------------|------|
| `NotFoundError` | ❌ | 往上找 |
| `AppException` | ✅ | 用 `app_exception_handler` |

因此不必為每個子類別各註冊一次,**只 import 並註冊父類別 `AppException` 就夠了**。

---

## handler 註冊順序與比對規則

四個 handler,由「最精確」到「兜底」:

| Handler | 接住的例外 | 回應狀態碼 | 用途 |
|---------|-----------|-----------|------|
| `app_exception_handler` | `AppException` 及子類別 | `exc.status_code` | 業務層錯誤;5xx 記 error+traceback,4xx 記 warning |
| `validation_exception_handler` | `RequestValidationError` | 422 | Pydantic/FastAPI 請求驗證失敗 |
| `http_exception_handler` | `HTTPException` | `exc.status_code` | 取代 FastAPI 預設的 HTTPException 回應 |
| `unhandled_exception_handler` | `Exception` | 500 | 兜底 (catch-all),攔所有沒被前面接住的錯誤 |

- 由於 MRO 是「由子往父找第一個命中」,更精確的類別一定先命中,不會誤落到 `Exception` 兜底。
- `unhandled_exception_handler` 只有在 `app_debug=True` 時才回傳 traceback / 詳細訊息;正式環境 (`app_debug=False`) 隱藏細節,避免洩漏機密。

---

## 使用方式

在業務邏輯(service / router)中直接 raise:

```python
from app.core.exceptions import NotFoundError, BusinessRuleError

def get_user(user_id: int):
    user = repo.find(user_id)
    if user is None:
        raise NotFoundError("使用者不存在", details={"user_id": user_id})
    return user
```

在 app 啟動時註冊一次(通常在 `app.py`):

```python
from app.core.exceptions import setup_exception_handlers

setup_exception_handlers(app)
```

---

## 回應格式

所有 handler 都經由 `_build_response()` 產生一致的 JSON:

```json
{
  "error": "not_found",
  "message": "使用者不存在",
  "request_id": "…",
  "details": { "user_id": 1 }
}
```

- `details` 只有在非空時才出現。
- `debug_info` 只在 `unhandled_exception_handler` 且 `app_debug=True` 時出現。
- `request_id` 來自 `request_id_ctx`(request_id middleware 設定)。

---

## 與其他框架的比較

「父類別能接住子類別」是 **Python 語言層級**的行為(`try/except` 就是 isinstance 比對),到哪都成立。但「怎麼把例外對應到 handler」是**各框架自己實作**的:

| 框架 | 機制 | 是否走 MRO 繼承鏈 |
|------|------|-------------------|
| **FastAPI / Starlette** | `add_exception_handler(cls, fn)`,查 dict + MRO | ✅ |
| **Flask** | `@app.errorhandler(cls)`,`_find_error_handler` 走 MRO;另可用狀態碼數字當 key、可綁 blueprint | ✅(概念相同,多了狀態碼 key 與 blueprint 作用域) |
| **Django** | middleware 的 `process_exception` 鏈,或 `handler404` / `handler500` | ❌(不同模型) |

結論:**繼承比對的底層原理一樣,但框架把它包成 handler 的方式各不相同。**
