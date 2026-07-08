from .base import BaseAppSettings
from app.core.enums import LogLevel

class TestAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = LogLevel.WARNING

    # database (SQLite in-memory - 每次測試清乾淨)
    db_dialect: str = "sqlite+aiosqlite"
    db_name: str = ":memory:"
    database_echo: bool = False
