from .base import BaseAppSettings, LogLevel

class DevAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = "DEBUG"