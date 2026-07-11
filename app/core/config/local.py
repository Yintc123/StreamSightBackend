from app.core.enums import LogLevel

from .base import BaseAppSettings


class LocalAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = LogLevel.DEBUG

    # database
    db_dialect: str = "postgresql+asyncpg"
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_name: str = "fastapi_template_local"
    database_echo: bool = True

    # encryption_key: 從 .env 讀取（見 .env.example）
