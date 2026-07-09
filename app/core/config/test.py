from .base import BaseAppSettings
from app.core.enums import LogLevel

class TestAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = LogLevel.WARNING

    # database (SQLite in-memory - 每次測試清乾淨)
    db_dialect: str = "sqlite+aiosqlite"
    # ?cache=shared&uri=true 這是什麼意思？
    db_name: str = ":memory:?cache=shared&uri=true"
    database_echo: bool = False
