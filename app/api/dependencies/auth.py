"""
Auth-related FastAPI dependencies.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
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
