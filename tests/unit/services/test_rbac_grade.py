"""rbac §5.1/§8.3：簽發帶 grade（child 現值）、refresh 刷新 grade、set_tier/set_admin_role。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.enums import AdminRole, UserTier
from app.dtos import (
    AdminLoginRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    UserCreate,
)
from app.services import AdminService, AuthService, UserService


async def _seed_admin(
    db_session: AsyncSession,
    *,
    username: str,
    admin_role: AdminRole = AdminRole.VIEWER,
    password: str = "longpassword",
):
    return await AdminService(db_session).create(
        username=username, name="CMS", password=password, admin_role=admin_role
    )


# ── 簽發帶 grade ──


async def test_admin_login_token_grade_is_admin_role(db_session: AsyncSession) -> None:
    await _seed_admin(db_session, username="cms", admin_role=AdminRole.SUPER_ADMIN)
    resp = await AuthService(db_session).admin_login(
        AdminLoginRequest(username="cms", password="longpassword")
    )
    assert decode_token(resp.access_token)["grade"] == "super_admin"


async def test_user_login_token_grade_is_user_tier(db_session: AsyncSession) -> None:
    # 註冊一個 user（預設 tier=free），再登入
    auth = AuthService(db_session)
    await auth.register(RegisterRequest(email="u@example.com", name="U", password="longpassword"))
    resp = await auth.login(LoginRequest(email="u@example.com", password="longpassword"))
    assert decode_token(resp.access_token)["grade"] == "free"


async def test_register_token_has_grade(db_session: AsyncSession) -> None:
    resp = await AuthService(db_session).register(
        RegisterRequest(email="r@example.com", name="R", password="longpassword")
    )
    assert decode_token(resp.access_token)["grade"] == "free"


async def test_refresh_reflects_updated_admin_role(db_session: AsyncSession) -> None:
    """改 admin_role 後 refresh → 新 access 的 grade 反映新值（rotation 自動刷新）。"""
    admin = await _seed_admin(db_session, username="cms", admin_role=AdminRole.VIEWER)
    actor = await _seed_admin(db_session, username="root", admin_role=AdminRole.SUPER_ADMIN)
    auth = AuthService(db_session)
    login = await auth.admin_login(AdminLoginRequest(username="cms", password="longpassword"))
    assert login.refresh_token is not None
    assert decode_token(login.access_token)["grade"] == "viewer"

    await AdminService(db_session).set_admin_role(
        admin.id, admin_role=AdminRole.EDITOR, actor_principal_id=actor.principal_id
    )
    refreshed = await auth.refresh(RefreshRequest(refresh_token=login.refresh_token))
    assert decode_token(refreshed.access_token)["grade"] == "editor"


# ── set_tier / set_admin_role 寫入 child ──


async def test_set_tier_writes_and_returns(db_session: AsyncSession) -> None:
    user = await UserService(db_session).create(UserCreate(email="t@example.com", name="T"))
    updated = await UserService(db_session).set_tier(user.id, UserTier.PREMIUM)
    assert updated.user_tier == UserTier.PREMIUM.value


async def test_set_admin_role_writes_and_returns(db_session: AsyncSession) -> None:
    target = await _seed_admin(db_session, username="cms", admin_role=AdminRole.VIEWER)
    actor = await _seed_admin(db_session, username="root", admin_role=AdminRole.SUPER_ADMIN)
    updated = await AdminService(db_session).set_admin_role(
        target.id, admin_role=AdminRole.EDITOR, actor_principal_id=actor.principal_id
    )
    assert updated.admin_role == AdminRole.EDITOR.value
