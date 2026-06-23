from pydantic import Field, field_validator
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
    