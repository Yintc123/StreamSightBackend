import logging

from app.core.config import get_app_settings
from app.core.enums import LogLevel

_LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_LOG_DATEFMT: str = "%Y-%m-%d %H:%M:%S"

# 針對套件設定 log level
_NOISY_LOGGERS: dict[str, LogLevel] = {
    # "sqlalchemy.engine": LogLevel.WARNING,
    # "httpx": LogLevel.WARNING,
    # "urllib3": LogLevel.WARNING
}

def setup_logging() -> None:
    """
    設置 root logger 和套件的 log level。

    當設定 force=True 時，呼叫順序就不再是嚴格要求的；
    不過越早呼叫越好，因為這樣可以避免模組初始化期間產生的日誌訊息，
    被 Python 預設的「WARNING 以上等級輸出到 stderr」處理器所處理。
    """
    app_settings = get_app_settings()

    logging.basicConfig(
        level = app_settings.log_level,
        format = _LOG_FORMAT,
        datefmt = _LOG_DATEFMT,
        # uvicorn 啟動時會先設置 root logger 的 handler，
        # force = True 讓 basicConfig 取代 uvicorn 的 root logger handler
        force = True
    )

    for name, level in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(level)
