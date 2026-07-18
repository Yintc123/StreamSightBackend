"""Unit tests for app/core/enums：AdminRole／UserTier（權限等級 StrEnum）＋ rank 表。

rbac §3.1/§8.1。
"""

from app.core.enums import (
    ADMIN_ROLE_RANK,
    USER_TIER_RANK,
    AdminRole,
    AdminStatusFilter,
    UserTier,
)


def test_admin_role_string_values() -> None:
    assert AdminRole.SUPER_ADMIN.value == "super_admin"
    assert AdminRole.EDITOR.value == "editor"
    assert AdminRole.VIEWER.value == "viewer"


def test_admin_role_is_str_enum() -> None:
    # StrEnum：成員即字串，可直接與字串比較（DB 存字串值）
    assert AdminRole("viewer") is AdminRole.VIEWER
    assert AdminRole.VIEWER == "viewer"


def test_user_tier_string_values() -> None:
    assert UserTier.FREE.value == "free"
    assert UserTier.PREMIUM.value == "premium"


def test_user_tier_is_str_enum() -> None:
    assert UserTier("premium") is UserTier.PREMIUM
    assert UserTier.FREE == "free"


def test_admin_role_rank_is_ordered_ladder() -> None:
    # 權限高→低：super_admin > editor > viewer（供 require_min_admin_role 比較）
    assert ADMIN_ROLE_RANK[AdminRole.SUPER_ADMIN] > ADMIN_ROLE_RANK[AdminRole.EDITOR]
    assert ADMIN_ROLE_RANK[AdminRole.EDITOR] > ADMIN_ROLE_RANK[AdminRole.VIEWER]
    # 每個成員都要有 rank（避免遺漏）
    assert set(ADMIN_ROLE_RANK.keys()) == set(AdminRole)


def test_user_tier_rank_is_ordered_ladder() -> None:
    assert USER_TIER_RANK[UserTier.PREMIUM] > USER_TIER_RANK[UserTier.FREE]
    assert set(USER_TIER_RANK.keys()) == set(UserTier)


def test_admin_status_filter_values() -> None:
    # model §2.7/§4：列表狀態篩選（跨層共用）
    assert AdminStatusFilter.ACTIVE.value == "active"
    assert AdminStatusFilter.ARCHIVED.value == "archived"
    assert AdminStatusFilter.DELETED.value == "deleted"
    assert AdminStatusFilter.ALL.value == "all"
    assert AdminStatusFilter("active") is AdminStatusFilter.ACTIVE
