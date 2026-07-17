from enum import IntEnum, StrEnum


class Role(IntEnum):
    """Principal 型別判別子（account type discriminator）。

    對外是 JWT 的整數 `role` claim，故用 IntEnum（其餘 enum 對外是字串，用 StrEnum）。
    USER=0 為一般 App 使用者、ADMIN=1 為 CMS 管理者。存於 `principals.role`。
    見 docs/specs/jwt-role-and-admin.md §3.1。
    """

    USER = 0
    ADMIN = 1


# stage / test 預留，待建立對應 Settings 後加入 _ENV_MAP
class AppEnv(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGE = "stage"
    PRODUCTION = "production"
    TEST = "test"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
