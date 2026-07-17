from app.core.enums import LogLevel

from .base import BaseAppSettings


class LocalAppSettings(BaseAppSettings):
    # app
    app_debug: bool = True

    # logging
    log_level: LogLevel = LogLevel.DEBUG

    # database（連 infra 的 MariaDB，見 /infra/docker-compose.yml）
    db_dialect: str = "mysql+asyncmy"
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "streamsight"
    db_name: str = "streamsight"
    database_echo: bool = True

    # encryption_key: 從 .env 讀取（見 .env.example）
