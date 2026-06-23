from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Literal 用於限制變數只能是某幾個固定值，Literal alias 用駝峰是 Python 慣例（同 List/Dict/Optional）
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
# stage / test 預留，待建立對應 Settings 後加入 _ENV_MAP
AppEnv = Literal["local", "development", "stage", "production", "test"]

class BaseAppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        case_sensitive = False,
        extra = "ignore"
    )

    # app
    app_env: AppEnv = Field(default="local", description="local env")
    app_name: str = "fastapi-foundation-template"
    app_version: str = "1.0.0"
    app_debug: bool = False

    # logging
    log_level: LogLevel = Field(default="INFO", description="Root logger level")

    # app
    @field_validator("app_env", mode="before")
    @classmethod
    def _normalize_app_env(cls, validator: str) -> str:
        return validator.lower() if isinstance(validator, str) else validator

    # logging
    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, validator: str) -> str:
        return validator.upper() if isinstance(validator, str) else validator