from .base import BaseAppSettings, LogLevel

class LocalAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = "DEBUG"