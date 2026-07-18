from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import AdminService, AuthService, UserService

from .db import get_session


def get_admin_service(
    session: AsyncSession = Depends(get_session),
) -> AdminService:
    """FastAPI dependency: build an AdminService bound to the request's session."""
    return AdminService(session)


def get_user_service(
    session: AsyncSession = Depends(get_session),
) -> UserService:
    """FastAPI dependency: build a UserService bound to the request's session."""
    return UserService(session)


def get_auth_service(
    session: AsyncSession = Depends(get_session),
) -> AuthService:
    """FastAPI dependency: build an AuthService bound to the request's session."""
    return AuthService(session)
