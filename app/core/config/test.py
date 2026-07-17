from pydantic_settings import SettingsConfigDict

from app.core.enums import LogLevel

from .base import BaseAppSettings


class TestAppSettings(BaseAppSettings):
    # Tests 必須 hermetic：忽略本機 .env（否則開發者的 .env 會覆蓋下方 sqlite 預設），
    # 只吃 conftest.py 於 import 前設定的 os.environ（APP_ENV / 各 secret）。
    model_config = SettingsConfigDict(
        env_file=None, env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

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
