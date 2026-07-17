"""Unit tests for the initial-admin seed logic. §5.8.

seed 冪等：已存在則略過；email/password 為空則報錯。
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Admin
from scripts.create_admin import create_initial_admin


async def _count_admins(db_session: AsyncSession) -> int:
    return (await db_session.execute(select(func.count()).select_from(Admin))).scalar_one()


async def test_creates_admin_when_absent(db_session: AsyncSession) -> None:
    admin = await create_initial_admin(db_session, "seed@example.com", "longpassword")

    assert admin.id is not None
    assert admin.email == "seed@example.com"
    assert admin.role == 1


async def test_idempotent_skips_existing(db_session: AsyncSession) -> None:
    first = await create_initial_admin(db_session, "seed@example.com", "longpassword")
    second = await create_initial_admin(db_session, "seed@example.com", "longpassword")

    assert second.id == first.id
    assert await _count_admins(db_session) == 1


async def test_empty_credentials_raises(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await create_initial_admin(db_session, "", "")
