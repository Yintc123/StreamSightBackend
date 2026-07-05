# 日誌設定與使用慣例 (Logging)

本專案的 logging 架構與各模組的使用方式。設定程式碼位於 `app/core/logging.py`。

## 目錄

- [設計概觀](#設計概觀)
- [logger 是一棵全域共享的樹](#logger-是一棵全域共享的樹)
- [setup_logging():全域設定](#setup_logging全域設定)
- [各模組的使用慣例](#各模組的使用慣例)
- [log 格式與各欄位](#log-格式與各欄位)
- [request_id 是怎麼進到每筆 log 的](#request_id-是怎麼進到每筆-log-的)
- [針對特定套件調整 level](#針對特定套件調整-level)
- [常見問答](#常見問答)

---

## 設計概觀

- **全域只有一份設定**,在 `setup_logging()` 裡對 **root logger** 完成(handler / formatter / filter / level)。
- **各模組不重複設定**,只在檔案頂端取得一個「有名字的 logger」:`logging.getLogger(__name__)`。
- 各模組的 logger **沒有自己的設定**,log 會往上冒泡 (propagate) 到 root,由 root 的 handler 實際輸出。

---

## logger 是一棵全域共享的樹

Python logging 內部有一個**全域單一的 logger 註冊表**。所有 `getLogger(名字)` 都是從同一棵樹拿節點,名字用 `.` 分層:

```
root                              ← setup_logging() 設定這裡
└── app
    └── core
        └── exceptions
            └── handlers          ← getLogger("app.core.exceptions.handlers")
```

`__name__` 在每個檔案裡會自動等於該模組的路徑字串:

| 檔案 | `__name__` |
|------|-----------|
| `app/core/exceptions/handlers.py` | `app.core.exceptions.handlers` |
| `app/api/routers/health/health.py` | `app.api.routers.health.health` |

子節點沒有自己的 handler / level 時,會**繼承**父節點(最終到 root)的設定。

---

## setup_logging():全域設定

```python
def setup_logging() -> None:
    app_settings = get_app_settings()

    handler = logging.StreamHandler()                 # 輸出到 stderr
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, _LOG_DATEFMT))
    handler.addFilter(_RequestIdFilter())             # 注入 request_id

    logging.basicConfig(
        level=app_settings.log_level,
        handlers=[handler],
        force=True,                                   # 取代 uvicorn 預設的 root handler
    )

    for name, level in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(level)
```

重點:

- 設定的對象是 **root logger**,全 app 共用這一份。
- `force=True`:uvicorn 啟動時會先幫 root 掛 handler,`force=True` 讓 `basicConfig` **取代**它,確保用我們自己的格式。
- **越早呼叫越好**(通常在 `app.py` 啟動早期)。若在 `setup_logging()` 之前就有人印 log,Python 會觸發**隱式的預設 basicConfig**,用陽春格式輸出、且沒有 request_id filter。

---

## 各模組的使用慣例

每個要印 log 的檔案,**在 import 區塊下方、模組層級**加一行:

```python
import logging

logger = logging.getLogger(__name__)
```

之後模組內直接用:

```python
logger.info("使用者登入 %s", user_id)
logger.warning("重試第 %s 次", n)
logger.error("處理失敗", exc_info=True)     # 帶 traceback
```

### 規則

- ✅ **只**寫 `getLogger(__name__)` 這一行,**不要**在各模組重設 handler / formatter / level(那是 `setup_logging()` 的事)。
- ✅ 放在**模組層級**(檔案頂端),不要放進 function 裡。
- ✅ 用 `%` 佔位參數(`logger.info("x=%s", x)`)而非 f-string——延遲字串格式化,level 未達時不會浪費運算。
- ✅ 例外情境用 `logger.exception(...)` 或 `exc_info=True` 才會附上 traceback;`logger.exception` 只能在 `except` 區塊內用。

### 為什麼不直接用 `logging.error(...)`

`logging.error(...)` 是拿 **root logger** 印,雖然也能輸出,但:

| | `getLogger(__name__)` | 直接 `logging.error(...)` |
|---|---|---|
| `%(name)s` 顯示 | 模組路徑 | 一律 `root`(分不出來源) |
| request_id | ✅ | ✅ |
| 按模組調 level / 過濾 | ✅ | ❌ |
| 隱式 basicConfig 時機風險 | 無 | 有(若早於 `setup_logging`) |

結論:多打一行 `getLogger(__name__)`,換到**來源標示 + 精細控制**,是慣例也是最佳解。

---

## log 格式與各欄位

```python
_LOG_FORMAT  = "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s - %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
```

輸出範例:

```
2026-07-05 12:00:00 [ERROR] [req-abc123] app.core.exceptions.handlers - AppException: ...
```

| 欄位 | 來源 |
|------|------|
| `%(asctime)s` | 時間(`_LOG_DATEFMT` 格式) |
| `%(levelname)s` | `INFO` / `WARNING` / `ERROR` … |
| `%(request_id)s` | `_RequestIdFilter` 從 ContextVar 注入 |
| `%(name)s` | logger 名字(= `getLogger` 傳入的名字) |
| `%(message)s` | log 內容 |

> **模組名稱要印出來,需兩個條件同時成立:**
> 1. formatter 有 `%(name)s`(決定「顯示」);
> 2. 用 `getLogger(__name__)`(決定名字「有意義」,而非 `root`)。
> 缺一就會看不到、或只看到 `root`。

---

## request_id 是怎麼進到每筆 log 的

`request_id` **不是** logger 內建欄位,而是自訂 filter 注入的:

```python
class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()   # 從 ContextVar 取值塞進 record
        return True                                 # True=保留這筆 log
```

流程:

```
每筆 log 產生 LogRecord
        ↓
handler 上的 _RequestIdFilter 執行 → 幫 record 加上 record.request_id
        ↓
formatter 用 %(request_id)s 把它撈出來印
```

Filter 掛在 **handler** 上(不是 logger),所以只要 log 經過 root 的 handler 就會被注入——即使某處誤用 `logging.error(...)`,request_id 一樣有。

`request_id_ctx` 由 request_id middleware 在每個請求開始時設定。

---

## 針對特定套件調整 level

因為是樹狀結構,設定某個節點的 level 會影響它底下所有子節點:

```python
_NOISY_LOGGERS: dict[str, LogLevel] = {
    # "sqlalchemy.engine": LogLevel.WARNING,
    # "httpx": LogLevel.WARNING,
    # "urllib3": LogLevel.WARNING,
}
```

想壓低某第三方套件的雜訊,取消註解即可。這種「按名字精細控制」只有在各處都用 `getLogger(名字)` 命名時才辦得到。

---

## 常見問答

**Q：各模組的 logger 是各自一份設定嗎?**
不是。全域只有 root 一份設定,各模組的 logger 沒有自己的設定,靠繼承 root。模組專屬的只有**名字**。

**Q：只 `import logging` 直接 `logging.error(...)` 會怎樣?**
能印,但 `%(name)s` 會變 `root`,且失去按模組調 level / 過濾的能力。見上表。

**Q：`getLogger(__name__)` 要放哪?**
每個要印 log 的檔案頂端、模組層級各一行。設定不用重寫,只要這一行。

**Q：前提是什麼?**
app 啟動早期要呼叫過一次 `setup_logging()`(設定 root)。之後所有模組的 `getLogger(__name__)` 就能正常輸出。

---

相關文件:例外處理見 [`docs/exceptions.md`](./exceptions.md)(其中的 handler 就是用本文的 logging 慣例記錄錯誤)。
