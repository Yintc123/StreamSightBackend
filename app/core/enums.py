from enum import IntEnum, StrEnum


class AdminRole(StrEnum):
    """Admin 型別內的權限等級（有序階梯，高→低）。存於 admins.admin_role。

    僅為權限等級，與 principals 的型別判別子 Role（USER/ADMIN）不同層次。
    授權階梯（require_min_admin_role）與 grade claim 見 docs/specs/rbac.md。
    """

    SUPER_ADMIN = "super_admin"  # 全權，含管理其他 admin
    EDITOR = "editor"  # 內容編輯
    VIEWER = "viewer"  # 唯讀（最低權限，建立預設）


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
