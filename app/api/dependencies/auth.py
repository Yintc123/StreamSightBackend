"""
Auth-related FastAPI dependencies.
"""

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
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
    """
    auth_service: AuthService = AuthService(session)
    return await auth_service.get_user_from_token(token)
