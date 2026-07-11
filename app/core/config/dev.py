from app.core.enums import LogLevel

from .base import BaseAppSettings


class DevAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = LogLevel.DEBUG

    # encryption_key: 從部署環境變數讀取
