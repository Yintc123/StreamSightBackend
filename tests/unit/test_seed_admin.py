"""Unit tests for the initial-admin seed logic. §4.

seed 冪等（以 username 判斷）：已存在則略過；username/password 為空則報錯；
以 SUPER_ADMIN 建立（bootstrap admin 需能管理其他 admin）。
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole
from app.models import Admin
from scripts.create_admin import create_initial_admin


async def _count_admins(db_session: AsyncSession) -> int:
    return (await db_session.execute(select(func.count()).select_from(Admin))).scalar_one()


async def test_creates_admin_when_absent(db_session: AsyncSession) -> None:
    admin = await create_initial_admin(db_session, "root", "Administrator", "longpassword")

    assert admin.id is not None
    assert admin.username == "root"
    assert admin.name == "Administrator"
    assert admin.role == 1
    assert admin.admin_role == AdminRole.SUPER_ADMIN.value
    # seed 建立的 root 為受保護（「≥1 super_admin」不變式的唯一建立點，§3.7）
    assert admin.is_protected is True


async def test_name_defaults_to_username_when_empty(db_session: AsyncSession) -> None:
    admin = await create_initial_admin(db_session, "root", "", "longpassword")
    assert admin.name == "root"


async def test_idempotent_skips_existing(db_session: AsyncSession) -> None:
    first = await create_initial_admin(db_session, "root", "Administrator", "longpassword")
    second = await create_initial_admin(db_session, "root", "Administrator", "longpassword")

    assert second.id == first.id
    assert await _count_admins(db_session) == 1


async def test_empty_credentials_raises(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await create_initial_admin(db_session, "", "", "")
