"""Principal supertype: repository + DB 完整性（CASCADE / 複合 FK / role 值域）。

見 docs/specs/jwt-role-and-admin.md §8.2。測試走 SQLite create_all，conftest
已開 PRAGMA foreign_keys=ON，故複合 FK 與 CHECK 皆實際強制。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.models import Principal, RefreshToken, User
from app.repositories.principal import PrincipalRepository
from app.repositories.user import UserRepository


async def test_create_returns_row_with_id_and_role(db_session: AsyncSession) -> None:
    repo: PrincipalRepository = PrincipalRepository(db_session)

    principal: Principal = await repo.create(Role.ADMIN)

    assert principal.id is not None
    assert principal.role == 1


async def test_get_returns_principal(db_session: AsyncSession) -> None:
    repo: PrincipalRepository = PrincipalRepository(db_session)
    created: Principal = await repo.create(Role.USER)

    fetched: Principal | None = await repo.get(created.id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.role == 0


async def test_get_by_principal_id_returns_user(db_session: AsyncSession, alice: User) -> None:
    repo: UserRepository = UserRepository(db_session)

    user: User | None = await repo.get_by_principal_id(alice.principal_id)

    assert user is not None
    assert user.id == alice.id


async def test_cascade_delete_principal_removes_user_and_refresh_tokens(
    db_session: AsyncSession, alice: User
) -> None:
    """刪 principals 該列 → 對應 user 與其 refresh_tokens 一併消失（取代 app 層清理）。"""
    token: RefreshToken = RefreshToken(
        principal_id=alice.principal_id,
        token_hash="cascade-test-hash",
        family_id="fam-cascade",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(token)
    await db_session.flush()

    await db_session.execute(delete(Principal).where(Principal.id == alice.principal_id))
    await db_session.flush()

    user_left = (
        await db_session.execute(select(User).where(User.id == alice.id))
    ).scalar_one_or_none()
    token_left = (
        await db_session.execute(
            select(RefreshToken).where(RefreshToken.principal_id == alice.principal_id)
        )
    ).scalar_one_or_none()

    assert user_left is None
    assert token_left is None


async def test_composite_fk_rejects_type_role_mismatch(db_session: AsyncSession) -> None:
    """把 User（role 恆 0）掛到 role=1 的 principal → 複合 FK 擋下 IntegrityError。"""
    admin_principal: Principal = await PrincipalRepository(db_session).create(Role.ADMIN)

    mismatched: User = User(
        email="mismatch@example.com", name="Mismatch", principal_id=admin_principal.id
    )
    db_session.add(mismatched)

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_principals_role_domain_rejects_unknown_value(db_session: AsyncSession) -> None:
    """principals.role 值域硬化：role=5 無對應 child 型別 → CHECK 擋下。"""
    db_session.add(Principal(role=5))

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.parametrize("role_value", [0, 1])
async def test_principals_role_domain_accepts_valid_values(
    db_session: AsyncSession, role_value: int
) -> None:
    db_session.add(Principal(role=role_value))
    await db_session.flush()  # 不應拋錯
