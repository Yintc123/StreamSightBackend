from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.dtos import UserCreate, UserUpdate
from app.models import User
from app.services import UserService
from tests.payloads import user_payload


async def test_create_user(db_session: AsyncSession) -> None:
    service: UserService = UserService(db_session)
    payload: dict[str, Any] = user_payload("yin")
    user: User = await service.create(UserCreate(**payload))

    assert user.id is not None
    assert user.email == payload["email"]
    assert user.name == payload["name"]
    assert user.is_active is True
    assert user.created_at is not None


async def test_create_duplicate_email_raises_conflict(
    db_session: AsyncSession, alice: User
) -> None:
    service: UserService = UserService(db_session)
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
