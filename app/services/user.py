import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import mask_email
from app.dtos import UserCreate, UserUpdate
from app.models.user import User
from app.repositories.user import UserRepository

logger: logging.Logger = logging.getLogger(__name__)


class UserService:
    """
    Business logic for User domain.

    Transaction Boundary: each public method commits (or rolls back on error).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session
        self.repo: UserRepository = UserRepository(session)

    async def get(self, user_id: int) -> User:
        """Fetch a user by ID. Raises NotFoundError if missing."""
        user: User | None = await self.repo.get(user_id)
        if user is None:
            raise NotFoundError(f"User {user_id} not found")
        return user

    async def list_all(self, offset: int = 0, limit: int = 100) -> list[User]:
        """List users ordered by ID."""
        return await self.repo.list_all(offset=offset, limit=limit)

    async def create(self, payload: UserCreate) -> User:
        """Create a new user. Raises ConflictError if email is taken.

        認證 credential (password hash、OAuth sub) 存在 Identity 表、由 AuthService
        統一管理。此處只負責 User 這個實體。
        """
        if await self.repo.email_exists(payload.email):
            raise ConflictError(
                f"Email {mask_email(payload.email or '')} already registered",
                details={"field": "email"},
            )

        user: User = User(email=payload.email, name=payload.name)
        try:
            user = await self.repo.add(user)
            await self.session.commit()
            logger.info("Created user id=%s email=%s", user.id, mask_email(user.email or ""))
            return user
        except Exception:
            await self.session.rollback()
            # unhandled_exception_handler 回 500
            raise

    async def update(self, user_id: int, payload: UserUpdate) -> User:
        """Partially update a user. Only non-None fields are applied."""
        user: User = await self.get(user_id)

        updates: dict[str, Any] = payload.model_dump(exclude_unset=True)

        # 若改 email，先檢查唯一性
        # 保留兩層：內層 await 是有副作用的 DB query，比外層 pure comparison 昂貴，
        # 分開比合併成一個大 if 表達更清楚 short-circuit 意圖
        if "email" in updates and updates["email"] != user.email:  # noqa: SIM102
            if await self.repo.email_exists(updates["email"]):
                raise ConflictError(
                    f"Email {mask_email(updates['email'])} already registered",
                    details={"field": "email"},
                )

        for key, value in updates.items():
            setattr(user, key, value)

        try:
            await self.session.flush()
            await self.session.commit()
            await self.session.refresh(user)
            logger.info("Updated user id=%s fields=%s", user.id, list(updates.keys()))
            return user
        except Exception:
            await self.session.rollback()
            raise

    async def delete(self, user_id: int) -> None:
        """Delete a user by ID. Raises NotFoundError if missing."""
        user: User = await self.get(user_id)
        try:
            await self.repo.delete(user)
            await self.session.commit()
            logger.info("Deleted user id=%s email=%s", user_id, mask_email(user.email or ""))
        except Exception:
            await self.session.rollback()
            raise
