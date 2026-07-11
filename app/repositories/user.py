from sqlalchemy import Select, select
from sqlalchemy.engine import Result

from app.models.user import User

from .base import BaseRepository

class UserRepository(BaseRepository[User]):
    model: type[User] = User

    async def get_by_email(self, email: str) -> User | None:
        """Fetch a user by email address. Returns None if not found."""
        stmt: Select[tuple[User]] = select(User).where(User.email == email)
        result: Result[tuple[User]] = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        """Check if an email is already registered (case-sensitive)."""
        stmt: Select[tuple[int]] = select(User.id).where(User.email == email).limit(1)
        result: Result[tuple[int]] = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None