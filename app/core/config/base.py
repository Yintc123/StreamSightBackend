from urllib.parse import quote

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import AppEnv, LogLevel


class BaseAppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
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
        default="mysql+asyncmy",
        description="SQLAlchemy dialect+driver (mysql+asyncmy / postgresql+asyncpg / sqlite+aiosqlite)",
    )
    db_host: str = Field(default="localhost", description="DB host (ignored for SQLite)")
    db_port: int = Field(default=3306, ge=1, le=65535, description="DB port (ignored for SQLite)")
    db_user: str = Field(default="streamsight", description="DB user (ignored for SQLite)")
    # SecretStr 可以讓密碼不顯示於 log 中，db_password 顯示為 SecretStr('**********')
    db_password: SecretStr = Field(
        default=SecretStr(""),
        description="DB password (ignored for SQLite; use secret manager in prod)",
    )
    db_name: str = Field(default="streamsight", description="DB name (or SQLite file path)")

    # database - engine config
    database_echo: bool = Field(
        default=False,
        description="Log all SQL statements (dev only)",
    )
    database_pool_size: int = Field(
        default=5,
        ge=1,  # greater than or equal
        le=100,  # less than or equal
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

    # jwt
    jwt_secret_key: SecretStr = Field(
        default=SecretStr(""),
        description="JWT signing secret (>=32 chars; NEVER expose; rotate carefully)",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm (HS256 for symmetric, RS256 for asymmetric)",
    )
    # 這個專案對時間參數的設置單位統一用“秒”
    jwt_access_token_expire_seconds: int = Field(
        default=1800,
        ge=1,
        le=86400,  # 24 小時
        description="Access token expiry in minutes (default 30, max 24h)",
    )

    # refresh token（opaque token，非 JWT，故不加 jwt_ 前綴）
    refresh_token_expire_seconds: int = Field(
        default=1209600,  # 14 天
        ge=1,
        le=7776000,  # 上限 90 天
        description="Refresh token expiry in seconds (default 14d, max 90d)",
    )
    # refresh token 雜湊用的 pepper（HMAC-SHA256 key），與 jwt_secret_key 分離
    refresh_token_hash_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Server-side pepper for HMAC-hashing refresh tokens (>=32 chars; NEVER expose)",
    )
    # reuse 誤判緩解視窗：剛撤銷 N 秒內的重放視為良性並發/重試，只 401 不連坐 family
    refresh_token_reuse_grace_seconds: int = Field(
        default=10,
        ge=0,
        le=300,
        description="Grace window (seconds) where re-presenting a just-rotated token does not nuke the family",
    )

    # 初始 CMS admin（供 seed script 建立；app runtime 非必要，空值時 seed script 報錯）
    initial_admin_username: str = Field(
        default="", description="Initial CMS admin username (seed script only)"
    )
    initial_admin_name: str = Field(
        default="",
        description="Initial CMS admin display name (seed script only; empty → 用 username)",
    )
    initial_admin_password: SecretStr = Field(
        default=SecretStr(""), description="Initial CMS admin password (seed script only)"
    )

    # redis - connection fields
    redis_host: str = Field(default="localhost", description="Redis host")
    redis_port: int = Field(
        default=6379,
        ge=1,
        le=65535,
        description="Redis port",
    )
    redis_username: str = Field(
        default="", description="Redis username (Redis 6+ ACL only; leave empty for legacy auth)"
    )
    redis_password: SecretStr = Field(
        default=SecretStr(""),
        description="Redis password (empty for no auth; use secret manager in prod)",
    )
    redis_db: int = Field(default=0, ge=0, le=15, description="Redis logical DB number (0-15)")

    # redis - client config
    redis_pool_max_connections: int = Field(
        default=20, ge=1, le=1000, description="Max connections in the Redis client pool"
    )

    # @computed_field
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

    # @computed_field
    @property
    def redis_url(self) -> str:
        """
        Compose Redis URL from individual fields.

        Formats:
            no auth:                redis://host:port/db
            password only:          redis://:password@host:port/db
            ACL (user + password):  redis://user:password@host:port/db
        """
        password_raw: str = self.redis_password.get_secret_value()

        if not password_raw:
            return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

        # 密碼 URL-encode 防特殊字元
        password: str = quote(password_raw, safe="")
        # username 有值就 ACL 形式，否則 legacy 的形式 (開頭 `:password@`)
        auth: str = f"{self.redis_username}:{password}" if self.redis_username else f":{password}"
        return f"redis://{auth}@{self.redis_host}:{self.redis_port}/{self.redis_db}"

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

    @field_validator("encryption_key", mode="after")
    @classmethod
    def _validate_encryption_key(cls, value: SecretStr) -> SecretStr:
        raw: str = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError("encryption_key must be at least 32 characters")
        return value

    @field_validator("jwt_secret_key", mode="after")
    @classmethod
    def _validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        raw: str = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError("jwt_secret_key must be at least 32 characters")
        return value

    @field_validator("refresh_token_hash_secret", mode="after")
    @classmethod
    def _validate_refresh_token_hash_secret(cls, value: SecretStr) -> SecretStr:
        raw: str = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError("refresh_token_hash_secret must be at least 32 characters")
        return value
