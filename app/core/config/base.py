from urllib.parse import quote

from pydantic import Field, field_validator, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import AppEnv, LogLevel

class BaseAppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        case_sensitive = False,
        extra = "ignore"
    )

    # app
    app_env: AppEnv = Field(default=AppEnv.LOCAL, description="Application environment name")
    app_name: str = "fastapi-foundation-template"
    app_version: str = "1.0.0"
    app_debug: bool = False

    # logging
    log_level: LogLevel = Field(default=LogLevel.INFO, description="Root logger level")

    # database - connection fields
    db_dialect: str = Field(
        default="postgresql+asyncpg",
        description="SQLAlchemy dialect+driver (postgresql+asyncpg / sqlite+aiosqlite / mysql+aiomysql)"
    )
    db_host: str = Field(default="localhost", description="DB host (ignored for SQLite)")
    db_port: int = Field(default=5432, ge=1, le=65535, description="DB port (ignored for SQLite)")
    db_user: str = Field(default="postgres", description="DB user (ignored for SQLite)")
    # SecretStr 可以讓密碼不顯示於 log 中，db_password 顯示為 SecretStr('**********')
    db_password: SecretStr = Field(
        default=SecretStr(""),
        description="DB password (ignored for SQLite; use secret manager in prod)"
    )
    db_name: str = Field(default="app", description="DB name (or SQLite file path)")

    # database - engine config
    database_echo: bool = Field(
        default=False,
        description="Log all SQL statements (dev only)",
    )
    database_pool_size: int = Field(
        default=5,
        ge=1,       # greater than or equal
        le=100,     # less than or equal
        description="Connection pool size (ignored for SQLite)",
    )
    database_pool_recycle: int = Field(
        default=3600,
        description="Recycle connections after N seconds",
    )

    # column-level encryption
    encryption_key: SecretStr = Field(
        default=SecretStr(""),
        description="AES-256 key for column encryption (>=32 chars; NEVER change once data exists)",
    )

    @field_validator("encryption_key", mode="after")
    @classmethod
    def _validate_encryption_key(cls, value: SecretStr) -> SecretStr:
        raw: str = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError("encryption_key must be at least 32 characters")
        return value

    @computed_field
    @property
    def database_url(self) -> str:
        """
        Compose SQLAlchemy async URL from individual fields.

        SQLite: {dialect}:///{path}
        Others: {dialect}://{user}:{password}@{host}:{port}/{name}
        """
        if self.db_dialect.startswith("sqlite"):
            return f"{self.db_dialect}:///{self.db_name}"
        
        # 密碼用 quote 做 URL-encode，防特殊字元 (@ : / # % 等) 破壞 URL 解析
        # safe 預設為 "/"
        password: str = quote(self.db_password.get_secret_value(), safe="")
        return (
            f"{self.db_dialect}://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # app
    @field_validator("app_env", mode="before")
    @classmethod
    def _normalize_app_env(cls, value: str) -> str:
        return value.lower() if isinstance(value, str) else value

    # logging
    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.upper() if isinstance(value, str) else value
