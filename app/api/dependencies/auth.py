"""
Auth-related FastAPI dependencies.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    ExpiredSignatureError,
    InvalidTokenError,
    decode_token,
    extract_sid,
)
from app.core.enums import (
    ADMIN_ROLE_RANK,
    USER_TIER_RANK,
    AdminRole,
    Role,
    UserTier,
)
from app.core.exceptions import ForbiddenError
from app.dtos import CurrentPrincipal
from app.models import Admin, User
from app.services import AuthService

from .db import get_session

# Oauth2 password bearer schema:
#   - 從 Authorization: Bearer <token> header 讀取 token
#   - tokenUrl 提供 swagger UI 顯示登入表單和指向實際的 login endpoint
#   - auto_error=True：Authorization header 缺失時 FastAPI 直接回應 401 (不會執行 get_current_user)
_oauth2_schema: OAuth2PasswordBearer = OAuth2PasswordBearer(
    # 讓開發者可以從 swagger 做登入，拿取 JWT
    tokenUrl="/auth/login",
    auto_error=True,
)


async def get_current_user(
    token: str = Depends(_oauth2_schema),
    session: AsyncSession = Depends(get_session),
) -> User:
    """
    FastAPI dependency: resolve current user from the Authorization Bearer token.

    Raises:
        UnauthorizedError: token missing / expired / invalid / user gone / user inactive
        ForbiddenError: 已認證但角色非 user（role != 0）
    """
    auth_service: AuthService = AuthService(session)
    return await auth_service.get_user_from_token(token)


async def get_current_admin(
    token: str = Depends(_oauth2_schema),
    session: AsyncSession = Depends(get_session),
) -> Admin:
    """
    FastAPI dependency: resolve current admin from the Bearer token（限 role 1）。

    Raises:
        UnauthorizedError: token missing / expired / invalid / admin gone / inactive
        ForbiddenError: 已認證但角色非 admin（role != 1）
    """
    auth_service: AuthService = AuthService(session)
    return await auth_service.get_admin_from_token(token)


async def get_current_principal(
    token: str = Depends(_oauth2_schema),
    session: AsyncSession = Depends(get_session),
) -> CurrentPrincipal:
    """
    FastAPI dependency: (principal_id, role) 輕量值物件，**不查 DB**（logout-all 等用）。
    """
    auth_service: AuthService = AuthService(session)
    return await auth_service.get_principal_from_token(token)


async def get_current_token_sid(token: str = Depends(_oauth2_schema)) -> str | None:
    """從當次 access token 取 sid（= refresh family_id），供 WS ticket 綁 session（§2.11）。

    token 的有效性由並列的 `get_current_admin` 保證（無效則整個請求先 401）；此處僅解析
    sid，解不出（缺 claim／初始 admin／壞 token）回 None → ticket 不綁 sid、只受 principal 級 kick。
    """
    try:
        return extract_sid(decode_token(token))
    except (ExpiredSignatureError, InvalidTokenError):
        return None


def require_min_admin_role(minimum: AdminRole) -> Callable[..., Awaitable[Admin]]:
    """Factory：階梯授權——當前 admin 的 admin_role rank ≥ minimum 才放行。

    讀 **child 現值**（admin.admin_role，非 token grade claim）→ 降權即時生效、竄改
    claim 無用（rbac §5.3）。等級不足 → ForbiddenError(403)。
    """

    async def _dep(admin: Admin = Depends(get_current_admin)) -> Admin:
        if ADMIN_ROLE_RANK[AdminRole(admin.admin_role)] < ADMIN_ROLE_RANK[minimum]:
            raise ForbiddenError("insufficient admin role")
        return admin

    return _dep


def require_min_tier(minimum: UserTier) -> Callable[..., Awaitable[User]]:
    """Factory：階梯授權——當前 user 的 user_tier rank ≥ minimum 才放行。

    讀 child 現值（user.user_tier）→ 升降級即時。等級不足 → ForbiddenError(403)。
    """

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if USER_TIER_RANK[UserTier(user.user_tier)] < USER_TIER_RANK[minimum]:
            raise ForbiddenError("tier required")
        return user

    return _dep


def require_role(*roles: Role) -> Callable[..., Awaitable[CurrentPrincipal]]:
    """Factory：回傳一個檢查當前 principal 角色是否在允許集合內的 dependency。

    角色不符 → ForbiddenError(403)（已認證但越權），與 401（未認證）區分。
    """

    async def _dep(
        principal: CurrentPrincipal = Depends(get_current_principal),
    ) -> CurrentPrincipal:
        if principal.role not in roles:
            raise ForbiddenError("Insufficient role")
        return principal

    return _dep
