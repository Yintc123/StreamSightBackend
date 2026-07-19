"""Unit tests for app/core/enums：AdminRole／UserTier 為 IntEnum（rank = value）。

enum-int.md：rank 即 enum 值 → 支援 SQL 層比 rank、刪除分離的 rank dict。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole, AdminStatusFilter, UserTier
from app.models import Admin
from app.services import AdminService


def test_admin_role_int_values_and_order() -> None:
    # 間隙 5（供未來插值免重編號，enum-int.md）
    assert AdminRole.VIEWER == 0
    assert AdminRole.EDITOR == 5
    assert AdminRole.SUPER_ADMIN == 10
    # rank = value：直接比大小（取代舊 ADMIN_ROLE_RANK dict）
    assert AdminRole.SUPER_ADMIN > AdminRole.EDITOR > AdminRole.VIEWER


def test_user_tier_int_values_and_order() -> None:
    assert UserTier.FREE == 0
    assert UserTier.PREMIUM == 5
    assert UserTier.PREMIUM > UserTier.FREE


async def test_sql_level_rank_comparison(db_session: AsyncSession) -> None:
    """核心動機：DB 層 `WHERE admin_role >= EDITOR` 依 rank 篩選、`ORDER BY` 依權限序。

    StrEnum 下字串比較會誤配（'viewer' >= 'editor' 為真）→ 此測試對舊實作為 RED。
    """
    svc = AdminService(db_session)
    await svc.create(username="vwr", name="v", password="longpassword", admin_role=AdminRole.VIEWER)
    await svc.create(username="edt", name="e", password="longpassword", admin_role=AdminRole.EDITOR)
    await svc.create(
        username="spr", name="s", password="longpassword", admin_role=AdminRole.SUPER_ADMIN
    )

    rows = (
        (
            await db_session.execute(
                select(Admin.username)
                .where(Admin.admin_role >= AdminRole.EDITOR)
                .order_by(Admin.admin_role)
            )
        )
        .scalars()
        .all()
    )
    assert list(rows) == ["edt", "spr"]  # editor(5) + super_admin(10)，依 rank 序


def test_admin_status_filter_values() -> None:
    # AdminStatusFilter 維持 StrEnum（非有序 rank、非落地排序需求）
    assert AdminStatusFilter.ACTIVE.value == "active"
    assert AdminStatusFilter.ARCHIVED.value == "archived"
    assert AdminStatusFilter.DELETED.value == "deleted"
    assert AdminStatusFilter.ALL.value == "all"
    assert AdminStatusFilter("active") is AdminStatusFilter.ACTIVE
