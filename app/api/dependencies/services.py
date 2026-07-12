from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import UserService

from .db import get_session


def get_user_service(
    session: AsyncSession = Depends(get_session),
) -> UserService:
    """FastAPI dependency: build a UserService bound to the request's session."""
    return UserService(session)
