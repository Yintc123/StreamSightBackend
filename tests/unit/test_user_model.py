"""User model：user_tier 預設 FREE + CHECK 值域硬化。rbac §3.2/§8.2。"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role, UserTier
from app.models import Principal, User
from app.repositories.repo_principal import PrincipalRepository


async def _make_user(db_session: AsyncSession, *, name: str, user_tier: str | None = None) -> User:
    principal: Principal = await PrincipalRepository(db_session).create(Role.USER)
    kwargs: dict = {}
    if user_tier is not None:
        kwargs["user_tier"] = user_tier
    user: User = User(name=name, principal_id=principal.id, **kwargs)
    db_session.add(user)
    await db_session.flush()
    return user


async def test_user_tier_defaults_to_free(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, name="def")
    fetched = (await db_session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert fetched.user_tier == UserTier.FREE.value


async def test_user_tier_check_rejects_out_of_domain(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await _make_user(db_session, name="bad", user_tier="gold")
