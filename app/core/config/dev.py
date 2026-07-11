from .base import BaseAppSettings
from app.core.enums import LogLevel

class DevAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = LogLevel.DEBUG

    # encryption_key: 從部署環境變數讀取
