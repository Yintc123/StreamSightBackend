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
        description="SQLAlchemy dialect+driver (mysql+asyncmy / sqlite+aiosqlite)",
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

    # 初始 super admin（憑證存 SSM／config、**不進 DB**；恆為 super_admin、只發 access token、
    # 不可改密碼／鎖死）。兩者皆非空才啟用。登入後用來建立 DB admin（取代舊 seed 腳本）。
    # 密碼以 argon2id 雜湊形式存放（明文永不落地）。
    initial_admin_username: str = Field(
        default="",
        description="Initial super admin username (SSM-backed, not in DB; empty = disabled)",
    )
    initial_admin_name: str = Field(
        default="",
        description="Initial super admin display name (optional; empty → 用 username)",
    )
    initial_admin_password_hash: SecretStr = Field(
        default=SecretStr(""),
        description="Initial super admin argon2id password hash (SSM SecureString; empty = disabled)",
    )

    # ── WebSocket（Admin 即時推播，websocket §4.1）─────────────────────────
    # 時間一律「秒」（比照 jwt_access_token_expire_seconds）。無「連線最長壽命」上限（§2.2）。
    ws_ticket_ttl_seconds: int = Field(
        default=180,
        ge=1,
        le=3600,
        description="ticket 簽發後多久內須建立 WS（換票→開連線的寬限窗，非連線時長）",
    )
    ws_ping_interval_seconds: int = Field(
        default=30, ge=1, le=3600, description="server 送應用層 ping 的週期"
    )
    ws_missed_pong_limit: int = Field(
        default=2, ge=1, le=10, description="連續未回 pong 幾次判死 → close(4000)"
    )
    ws_idle_timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=86400,
        description="連線無任何進站訊息（含 pong）超時即關；須 > ping_interval",
    )
    ws_max_send_queue: int = Field(
        default=100,
        ge=1,
        le=100000,
        description="per-connection 有界送出佇列上限；滿（慢消費者）→ close(1013)",
    )
    ws_reauth_interval_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
        description="定期複查週期（is_active + session 有效性）；失效 → close(4401)",
    )
    ws_max_connections_per_principal: int = Field(
        default=10, ge=1, le=1000, description="單一 admin 最大同時連線數；超限拒絕（close 1013）"
    )
    ws_max_connections_total: int = Field(
        default=10000, ge=1, le=1000000, description="全實例最大連線數（防 DoS）；超限拒絕（1013）"
    )
    ws_max_message_bytes: int = Field(
        default=16384, ge=1, le=1048576, description="單一進站訊息大小上限；超過 → close(4400)"
    )
    ws_control_msg_rate_limit: int = Field(
        default=20, ge=1, le=10000, description="控制訊息速率上限（每連線 / 10s 滑動窗）"
    )
    ws_cid_max_length: int = Field(
        default=64, ge=1, le=256, description="cid 清洗上限（字元集 [A-Za-z0-9_-]）"
    )
    ws_allowed_origins: list[str] = Field(
        default_factory=list,
        description="handshake Origin 允許清單（防 CSWSH）；空＝依環境（語意於實作定案）",
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

    # ── Monitoring（monitoring.md §4.1）───────────────────────────────────────
    monitoring_enabled: bool = Field(
        default=True,
        description="監控總開關；False → 不掛 log handler、不起 sampler（測試/精簡部署）",
    )
    monitoring_log_stream_maxlen: int = Field(
        default=10000, ge=100, description="monitor:stream:logs 近似上限（MAXLEN ~）"
    )
    monitoring_log_queue_maxsize: int = Field(
        default=1000, ge=10, description="log handler 記憶體佇列上限；滿即丟最舊"
    )
    monitoring_log_flush_interval_seconds: int = Field(
        default=1, ge=1, le=60, description="背景 flush 週期（秒）"
    )
    monitoring_log_flush_batch_size: int = Field(
        default=100, ge=1, le=10000, description="單次 flush 批次上限"
    )
    monitoring_log_push_enabled: bool = Field(
        default=False,
        description="是否對 monitor.logs WS topic 即時 tail 推播（高頻，預設關）",
    )
    monitoring_db_sample_interval_seconds: int = Field(
        default=15, ge=1, le=3600, description="DB 狀態採樣週期（秒）"
    )
    monitoring_db_stream_maxlen: int = Field(
        default=10000, ge=100, description="monitor:stream:db 近似上限（MAXLEN ~）"
    )
    monitoring_sampler_leader_lease_seconds: int = Field(
        default=30,
        ge=2,
        description="Redis leader lease 時長（秒）；須 ≥ 2× db_sample_interval",
    )
    monitoring_query_max_limit: int = Field(
        default=500, ge=1, le=10000, description="查詢單頁上限（防重負載）"
    )
    monitoring_retention_seconds: int = Field(
        default=604800,
        ge=0,
        description="可選按時間修剪（MINID，秒）；0 = 只靠 MAXLEN",
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
