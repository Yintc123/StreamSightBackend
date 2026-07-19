from enum import IntEnum, StrEnum


class AdminRole(IntEnum):
    """Admin 型別內的權限等級（有序階梯，rank = value）。存於 admins.admin_role（SmallInteger）。

    IntEnum：值即 rank，可直接比大小（`role >= AdminRole.EDITOR`），並支援 SQL 層比 rank
    （`WHERE admin_role >= 50`、`ORDER BY`）。大間隙（0/50/100/999）供未來插值免重編號；
    ROOT 為階梯天花板（bootstrap root，is_protected）。上限不變式：admin_role < 1000。
    與 principals 的型別判別子 Role（USER/ADMIN）不同層次。見 docs/specs/enum-int.md、rbac.md。
    """

    VIEWER = 0  # 唯讀（最低權限，建立預設）
    EDITOR = 50  # 內容編輯
    SUPER_ADMIN = 100  # 全權，含管理其他 admin
    ROOT = 999  # bootstrap root（is_protected）；天花板，供 root-only API gating


class UserTier(IntEnum):
    """一般 User 的等級（有序階梯，rank = value）。存於 users.user_tier（SmallInteger）。

    IntEnum：值即 rank，直接比大小、支援 SQL 層篩選。FREE 為最低（建立預設 fail-safe）。
    實際分級待商業面確認（見 rbac §11）。授權階梯（require_min_tier）見 docs/specs/rbac.md §5.3。
    """

    FREE = 0
    PREMIUM = 5


class AdminStatusFilter(StrEnum):
    """Admin 列表狀態篩選（跨 service／repository／api 共用）。

    以時間戳謂詞對應（is_active 為計算屬性、不可進 SQL）。見 admin-management-model §2.7。
    """

    ACTIVE = "active"  # archived_at IS NULL AND deleted_at IS NULL
    ARCHIVED = "archived"  # archived_at IS NOT NULL AND deleted_at IS NULL
    DELETED = "deleted"  # deleted_at IS NOT NULL
    ALL = "all"  # 不篩


class RecordSortField(StrEnum):
    """Record 可排序欄位白名單，值對映前端 SORTABLE（契約不變，records-model.md §1/§4）。

    封閉白名單、驗證單一真相（service `_parse_sort`）；repo 收 enum 即不可能非法欄名。
    **不含 updated_at**（前端 SORTABLE 無此欄，records 亦無 updated_by，records-model §2.8）。
    """

    ID = "id"
    TITLE = "title"
    VALUE = "value"
    CATEGORY = "category"
    CREATED_AT = "created_at"


class SortDirection(StrEnum):
    """排序方向（型別化，供 service 解析與 repo 收參）。"""

    ASC = "asc"
    DESC = "desc"


# list 未帶 sort 時套用（＝前端 DEFAULT_SORT，records-model.md §1）
DEFAULT_SORT = "id:asc"


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
