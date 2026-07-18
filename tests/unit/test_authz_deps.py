"""rbac §5.3/§8.4：require_min_admin_role / require_min_tier 階梯授權（讀 child 現值）。

單元層直接以構造好的 child 物件呼叫 dependency 內函式（繞過 DI）——證明授權判定
讀的是 child 欄位（admin_role / user_tier），與 token claim 無關。
"""

import pytest

from app.api.dependencies.auth import require_min_admin_role, require_min_tier
from app.core.enums import AdminRole, UserTier
from app.core.exceptions import ForbiddenError
from app.models import Admin, User


def _admin(admin_role: AdminRole) -> Admin:
    return Admin(
        username="a", name="A", password_hash="h", principal_id=1, admin_role=admin_role.value
    )


def _user(tier: UserTier) -> User:
    return User(name="U", principal_id=1, user_tier=tier.value)


# ── require_min_admin_role ──


async def test_require_editor_rejects_viewer() -> None:
    dep = require_min_admin_role(AdminRole.EDITOR)
    with pytest.raises(ForbiddenError):
        await dep(admin=_admin(AdminRole.VIEWER))


async def test_require_editor_allows_editor_and_super() -> None:
    dep = require_min_admin_role(AdminRole.EDITOR)
    assert (await dep(admin=_admin(AdminRole.EDITOR))).admin_role == "editor"
    assert (await dep(admin=_admin(AdminRole.SUPER_ADMIN))).admin_role == "super_admin"


async def test_require_super_admin_rejects_editor() -> None:
    dep = require_min_admin_role(AdminRole.SUPER_ADMIN)
    with pytest.raises(ForbiddenError):
        await dep(admin=_admin(AdminRole.EDITOR))


async def test_require_super_admin_allows_super_admin() -> None:
    dep = require_min_admin_role(AdminRole.SUPER_ADMIN)
    assert (await dep(admin=_admin(AdminRole.SUPER_ADMIN))).admin_role == "super_admin"


# ── require_min_tier ──


async def test_require_premium_rejects_free() -> None:
    dep = require_min_tier(UserTier.PREMIUM)
    with pytest.raises(ForbiddenError):
        await dep(user=_user(UserTier.FREE))


async def test_require_premium_allows_premium() -> None:
    dep = require_min_tier(UserTier.PREMIUM)
    assert (await dep(user=_user(UserTier.PREMIUM))).user_tier == "premium"
