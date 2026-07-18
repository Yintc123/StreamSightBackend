from enum import IntEnum, StrEnum


class AdminRole(StrEnum):
    """Admin 型別內的權限等級（有序階梯，高→低）。存於 admins.admin_role。

    僅為權限等級，與 principals 的型別判別子 Role（USER/ADMIN）不同層次。
    授權階梯（require_min_admin_role）與 grade claim 見 docs/specs/rbac.md。
    """

    SUPER_ADMIN = "super_admin"  # 全權，含管理其他 admin
    EDITOR = "editor"  # 內容編輯
    VIEWER = "viewer"  # 唯讀（最低權限，建立預設）


class UserTier(StrEnum):
    """一般 User 的等級（有序階梯）。存於 users.user_tier；對外為 JWT grade claim。

    FREE 為最低（建立預設 fail-safe）。實際分級待商業面確認（見 rbac §11）。
    授權階梯（require_min_tier）見 docs/specs/rbac.md §5.3。
    """

    FREE = "free"
    PREMIUM = "premium"


# 權限高→低（值越大權限越高）；供 require_min_admin_role 階梯比較。見 rbac §3.1。
ADMIN_ROLE_RANK: dict[AdminRole, int] = {
    AdminRole.SUPER_ADMIN: 2,
    AdminRole.EDITOR: 1,
    AdminRole.VIEWER: 0,
}
# user 側等級階梯；供 require_min_tier 比較。
USER_TIER_RANK: dict[UserTier, int] = {
    UserTier.FREE: 0,
    UserTier.PREMIUM: 1,
}


class AdminStatusFilter(StrEnum):
    """Admin 列表狀態篩選（跨 service／repository／api 共用）。

    以時間戳謂詞對應（is_active 為計算屬性、不可進 SQL）。見 admin-management-model §2.7。
    """

    ACTIVE = "active"  # archived_at IS NULL AND deleted_at IS NULL
    ARCHIVED = "archived"  # archived_at IS NOT NULL AND deleted_at IS NULL
    DELETED = "deleted"  # deleted_at IS NOT NULL
    ALL = "all"  # 不篩


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
