from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.core.exceptions import ConflictError, NotFoundError
from app.dtos import UserCreate, UserUpdate
from app.models import Principal, RefreshToken, User
from app.repositories.principal import PrincipalRepository
from app.services import UserService
from tests.payloads import user_payload


async def _count_principals(db_session: AsyncSession) -> int:
    return (await db_session.execute(select(func.count()).select_from(Principal))).scalar_one()


async def test_create_user(db_session: AsyncSession) -> None:
    service: UserService = UserService(db_session)
    payload: dict[str, Any] = user_payload("yin")
    user: User = await service.create(UserCreate(**payload))

    assert user.id is not None
    assert user.email == payload["email"]
    assert user.name == payload["name"]
    assert user.is_active is True
    assert user.created_at is not None


async def test_user_can_be_created_without_email(db_session: AsyncSession) -> None:
    """OAuth 用戶場景:某些 provider (如 Apple sign in) 可能不提供 email。

    UserService.create() 走 UserCreate DTO 一定要 email、但底層 User model
    允許 email=None (為未來 OAuth flow 直接建 User 準備)。

    注意：User 現在必須掛上 principal（見 jwt-role-and-admin 規格），故先建 principal。
    """
    principal: Principal = await PrincipalRepository(db_session).create(Role.USER)
    user: User = User(email=None, name="OAuth User", principal_id=principal.id)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    assert user.id is not None
    assert user.email is None
    assert user.name == "OAuth User"
    assert user.principal_id == principal.id


async def test_delete_user_cascades_via_principal(db_session: AsyncSession, alice: User) -> None:
    """delete(user_id) → 刪 principals 該列，CASCADE 連帶清 user + refresh_tokens，無孤兒 principal。"""
    token: RefreshToken = RefreshToken(
        principal_id=alice.principal_id,
        token_hash="del-user-hash",
        family_id="fam-del",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(token)
    await db_session.commit()
    principals_before: int = await _count_principals(db_session)
    service: UserService = UserService(db_session)

    await service.delete(alice.id)

    assert await service.repo.get(alice.id) is None
    assert (
        await _count_principals(db_session) == principals_before - 1
    )  # principal 亦被刪（無孤兒）
    token_left = (
        await db_session.execute(
            select(RefreshToken).where(RefreshToken.principal_id == alice.principal_id)
        )
    ).scalar_one_or_none()
    assert token_left is None


async def test_create_duplicate_email_raises_conflict(
    db_session: AsyncSession, alice: User
) -> None:
    service: UserService = UserService(db_session)
    assert alice.email is not None
    duplicate: UserCreate = UserCreate(email=alice.email, name="duplicate")

    with pytest.raises(ConflictError) as exc_info:
        await service.create(duplicate)

    assert exc_info.value.details == {"field": "email"}
    assert exc_info.value.error_code == ConflictError.error_code


async def test_get_noneexistent_raises_not_found(db_session: AsyncSession) -> None:
    service: UserService = UserService(db_session)

    with pytest.raises(NotFoundError) as exc_info:
        await service.get(99999)

    assert exc_info.value.error_code == NotFoundError.error_code


async def test_update_only_provided_fields(db_session: AsyncSession, alice: User) -> None:
    service: UserService = UserService(db_session)
    assert alice.email is not None
    original_email: str = alice.email

    updated: User = await service.update(alice.id, UserUpdate(name="alice renamed"))

    assert updated.name == "alice renamed"
    assert updated.email == original_email
    assert updated.is_active is True


async def test_update_email_to_same_value_is_noop(db_session: AsyncSession, alice: User) -> None:
    service: UserService = UserService(db_session)

    # 改成一樣的 email 不應該出現 ConflictError
    updated: User = await service.update(alice.id, UserUpdate(email=alice.email))

    assert updated.email == alice.email


async def test_update_to_existing_email_raises_conflict(
    db_session: AsyncSession, alice: User, bob: User
) -> None:
    service: UserService = UserService(db_session)

    with pytest.raises(ConflictError):
        await service.update(bob.id, UserUpdate(email=alice.email))


async def test_deactivate_user(db_session: AsyncSession, alice: User) -> None:
    """Disable an account by setting is_active=False."""
    service: UserService = UserService(db_session)
    assert alice.is_active is True

    updated: User = await service.update(alice.id, UserUpdate(is_active=False))

    assert updated.is_active is False
    assert updated.email == alice.email


async def test_delete_remove_user(db_session: AsyncSession, alice: User) -> None:
    service: UserService = UserService(db_session)

    await service.delete(alice.id)

    with pytest.raises(NotFoundError):
        await service.get(alice.id)


async def test_delete_nonexistent_raises_not_found(db_session: AsyncSession) -> None:
    service: UserService = UserService(db_session)

    with pytest.raises(NotFoundError):
        await service.delete(99999)


async def test_list_return_all_users(db_session: AsyncSession, sample_users: list[User]) -> None:
    service: UserService = UserService(db_session)

    users: list[User] = await service.list_all()

    assert len(users) == len(sample_users)
