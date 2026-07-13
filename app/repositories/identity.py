from sqlalchemy import Select, select
from sqlalchemy.engine import Result

from app.models.identity import Identity

from .base import BaseRepository


class IdentityRepository(BaseRepository[Identity]):
    model: type[Identity] = Identity

    async def get_by_user_and_provider(self, user_id: int, provider: str) -> Identity | None:
        """Fetch a user's identity for a specific provider (e.g. 'password')."""
        stmt: Select[tuple[Identity]] = select(Identity).where(
            Identity.user_id == user_id,
            Identity.provider == provider,
        )
        result: Result[tuple[Identity]] = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_provider_and_sub(
        self, provider: str, provider_user_id: str
    ) -> Identity | None:
        """Fetch identity by provider + provider_user_id (OAuth sub).

        用於 OAuth login flow:給定 Google 回來的 sub、找對應的 identity。
        """
        stmt: Select[tuple[Identity]] = select(Identity).where(
            Identity.provider == provider,
            Identity.provider_user_id == provider_user_id,
        )
        result: Result[tuple[Identity]] = await self.session.execute(stmt)
        return result.scalar_one_or_none()
