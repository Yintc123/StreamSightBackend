import logging
import re

from app.core.config import BaseAppSettings, get_app_settings
from app.core.context import request_id_ctx
from app.core.enums import LogLevel
from app.core.security import mask_email

_LOG_FORMAT: str = "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s - %(message)s"
_LOG_DATEFMT: str = "%Y-%m-%d %H:%M:%S"

# 針對套件設定 log level
_NOISY_LOGGERS: dict[str, LogLevel] = {
    # "sqlalchemy.engine": LogLevel.WARNING,
    # "httpx": LogLevel.WARNING,
    # "urllib3": LogLevel.WARNING
}

# logging.Filter 是攔截器，掛在 handler 上時，在 formatter 處理 record 之前執行
class _RequestIdFilter(logging.Filter):
    # 從 ContextVar 取得 request_id 並且注入到每筆 log

    def filter(self, record: logging.LogRecord) -> bool:
        # LogRecord 是「一筆 log 的資料容器」，record 新增 request_id 屬性，將 request_id 注入每一筆 record
        record.request_id = request_id_ctx.get()
        # True 保留 / False 丟棄這筆 log
        return True


# 自動遮罩 log 內出現的 email（service 層已用 mask_email，這是防漏）
_EMAIL_RE: re.Pattern[str] = re.compile(
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
)


class _EmailMaskFilter(logging.Filter):
    """Auto-mask email addresses in log message and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _EMAIL_RE.sub(lambda m: mask_email(m.group(0)), record.msg)
        if record.args:
            record.args = tuple(
                _EMAIL_RE.sub(lambda m: mask_email(m.group(0)), a)
                if isinstance(a, str) else a
                for a in record.args
            )
        return True

def setup_logging() -> None:
    """
    設置 root logger 和套件的 log level。

    當設定 force=True 時，呼叫順序就不再是嚴格要求的；
    不過越早呼叫越好，因為這樣可以避免模組初始化期間產生的日誌訊息，
    被 Python 預設的「WARNING 以上等級輸出到 stderr」處理器所處理。
    """
    app_settings: BaseAppSettings = get_app_settings()

    # StreamHandler 把 log 寫到 stream（預設 sys.stderr），console 應用最常用
    handler: logging.Handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, _LOG_DATEFMT))
    handler.addFilter(_RequestIdFilter())
    handler.addFilter(_EmailMaskFilter())

    logging.basicConfig(
        level = app_settings.log_level,
        handlers=[handler],
        # format = _LOG_FORMAT,     # 用 handler 傳入
        # datefmt = _LOG_DATEFMT,   # 用 handler 傳入
        # uvicorn 啟動時會先設置 root logger 的 handler，
        # force = True 讓 basicConfig 取代 uvicorn 的 root logger handler
        force = True
    )

    for name, level in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(level)
