"""授權：token → user/admin/principal 的角色分流（fail-safe、403 vs 401）。§8.5。"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_role
from app.core.auth import create_access_token
from app.core.config import get_app_settings
from app.core.enums import Role
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.dtos import CurrentPrincipal, RegisterRequest
from app.models import Admin, User
from app.services import AdminService, AuthService


def _sign_no_role(sub: int) -> str:
    """以現行 secret 手簽一個**無 role claim** 的 access token（模擬舊版 token）。"""
    settings = get_app_settings()
    return jwt.encode(
        {
            "sub": str(sub),
            "type": "access",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


async def _seed_admin(db_session: AsyncSession, username: str = "cms") -> Admin:
    return await AdminService(db_session).create(
        username=username, name="CMS", password="longpassword"
    )


async def _register_user(db_session: AsyncSession, email: str = "u@example.com") -> User:
    auth = AuthService(db_session)
    await auth.register(RegisterRequest(email=email, name="U", password="longpassword"))
    user = await auth.user_service.repo.get_by_email(email)
    assert user is not None
    return user


# ── get_user_from_token: role 必須 0 ────────────────────────
async def test_get_user_from_token_rejects_admin_role(db_session: AsyncSession) -> None:
    admin = await _seed_admin(db_session)
    token = create_access_token(admin.principal_id, Role.ADMIN)

    with pytest.raises(ForbiddenError):
        await AuthService(db_session).get_user_from_token(token)


# ── get_admin_from_token ────────────────────────────────────
async def test_get_admin_from_token_returns_admin(db_session: AsyncSession) -> None:
    admin = await _seed_admin(db_session)
    token = create_access_token(admin.principal_id, Role.ADMIN)

    result = await AuthService(db_session).get_admin_from_token(token)

    assert result.id == admin.id


async def test_get_admin_from_token_rejects_user_role(db_session: AsyncSession) -> None:
    user = await _register_user(db_session)
    token = create_access_token(user.principal_id, Role.USER)

    with pytest.raises(ForbiddenError):
        await AuthService(db_session).get_admin_from_token(token)


async def test_get_admin_from_token_inactive_raises(db_session: AsyncSession) -> None:
    admin = await _seed_admin(db_session)
    # is_active 為計算屬性（archived_at/deleted_at 皆 NULL 才 True）→ 封存使其失效
    await AdminService(db_session).archive(admin.id)
    token = create_access_token(admin.principal_id, Role.ADMIN)

    with pytest.raises(UnauthorizedError):
        await AuthService(db_session).get_admin_from_token(token)


# ── get_principal_from_token（不查 DB）───────────────────────
async def test_get_principal_from_token_returns_value_object(db_session: AsyncSession) -> None:
    token = create_access_token(4242, Role.ADMIN)

    principal = await AuthService(db_session).get_principal_from_token(token)

    assert isinstance(principal, CurrentPrincipal)
    assert principal.id == 4242
    assert principal.role is Role.ADMIN


# ── require_role factory ────────────────────────────────────
async def test_require_role_allows_matching_role() -> None:
    dep = require_role(Role.ADMIN)
    principal = CurrentPrincipal(id=1, role=Role.ADMIN)

    result = await dep(principal=principal)

    assert result is principal


async def test_require_role_denies_mismatched_role() -> None:
    dep = require_role(Role.ADMIN)

    with pytest.raises(ForbiddenError):
        await dep(principal=CurrentPrincipal(id=1, role=Role.USER))


# ── 向後相容（fail-safe）：無 role claim → 視為 role 0 放行 ──
async def test_no_role_claim_token_treated_as_user(db_session: AsyncSession) -> None:
    """以現行 secret 簽出、缺 role claim 的 token → get_user_from_token 視為 role 0 放行。"""
    user = await _register_user(db_session, "norole@example.com")
    token = _sign_no_role(user.principal_id)

    result = await AuthService(db_session).get_user_from_token(token)

    assert result.id == user.id


# ── 過渡期安全（保留 id）：sub=舊 user.id、無 role → 解析到同一正確 user ──
async def test_preserve_id_backward_compat_resolves_same_user(db_session: AsyncSession) -> None:
    """保留 id（principal_id == user.id）下，舊 token（sub=user.id、無 role）解析到同一 user，

    不可能被解析成別人（釘死 §7 保留 id 過渡策略；不倚賴輪替 secret）。
    """
    user = await _register_user(db_session, "preserve@example.com")
    assert user.principal_id == user.id  # 佈局前提：第一個 user 的 principal_id == id
    token = _sign_no_role(user.id)  # 舊 token：sub = 舊 user.id

    result = await AuthService(db_session).get_user_from_token(token)

    assert result.id == user.id
