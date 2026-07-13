from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories.user import UserRepository


async def test_get_by_email_returns_user_when_exists(db_session: AsyncSession, alice: User) -> None:
    """Deterministic encryption 讓 WHERE email = plaintext 能命中已加密的欄位。"""
    repo: UserRepository = UserRepository(db_session)
    assert alice.email is not None

    user: User | None = await repo.get_by_email(alice.email)

    assert user is not None
    assert user.id == alice.id
    assert user.email == alice.email  # process_result_value 解密回 plaintext


async def test_get_by_email_returns_none_when_not_found(db_session: AsyncSession) -> None:
    repo: UserRepository = UserRepository(db_session)

    user: User | None = await repo.get_by_email("nobody@example.com")

    assert user is None


async def test_get_by_email_is_case_sensitive(db_session: AsyncSession, alice: User) -> None:
    """Deterministic encryption 是 byte-level 比對 — 大小寫不同的 email 視為不同 ciphertext。"""
    repo: UserRepository = UserRepository(db_session)
    assert alice.email is not None

    user: User | None = await repo.get_by_email(alice.email.upper())

    assert user is None


async def test_email_exists_returns_true_when_registered(
    db_session: AsyncSession, alice: User
) -> None:
    repo: UserRepository = UserRepository(db_session)
    assert alice.email is not None

    assert await repo.email_exists(alice.email) is True


async def test_email_exists_returns_false_when_not_registered(db_session: AsyncSession) -> None:
    repo: UserRepository = UserRepository(db_session)

    assert await repo.email_exists("nobody@example.com") is False


async def test_email_exists_is_case_sensitive(db_session: AsyncSession, alice: User) -> None:
    """Deterministic encryption 是 byte-level 比對 — 大小寫不同視為不存在。"""
    repo: UserRepository = UserRepository(db_session)
    assert alice.email is not None

    assert await repo.email_exists(alice.email.upper()) is False
