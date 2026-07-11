from .base import BaseAppSettings
from app.core.enums import LogLevel

class TestAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = LogLevel.WARNING

    # database (SQLite in-memory - 每次測試清乾淨)
    # 需搭配 conftest.py 的 StaticPool 讓所有 session 共用同一 connection
    db_dialect: str = "sqlite+aiosqlite"
    db_name: str = ":memory:"
    database_echo: bool = False

    # encryption_key: conftest.py 在 import 時設定 os.environ["ENCRYPTION_KEY"]
