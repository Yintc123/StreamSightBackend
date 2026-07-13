"""Unit tests for IdentityRepository + Identity model constraints.

覆蓋:
    - Repository lookups (by user+provider、by provider+sub)
    - UniqueConstraint 執行(user+provider、provider+sub)
    - Cascade delete (刪 user → 同步刪 identities)
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Identity, User
from app.repositories import IdentityRepository, UserRepository


async def _make_password_identity(
    session: AsyncSession, user: User, credential: str = "argon2_hash_stub"
) -> Identity:
    """Helper: 直接建 password identity（跳過 argon2、加速 test）。"""
    identity: Identity = Identity(user_id=user.id, provider="password", credential=credential)
    session.add(identity)
    await session.commit()
    return identity


async def _make_oauth_identity(
    session: AsyncSession, user: User, provider: str, sub: str
) -> Identity:
    """Helper: 建 OAuth identity。"""
    identity: Identity = Identity(user_id=user.id, provider=provider, provider_user_id=sub)
    session.add(identity)
    await session.commit()
    return identity


# ────────────────────────────────────────────────
# get_by_user_and_provider (password identity 查詢)
# ────────────────────────────────────────────────
async def test_get_by_user_and_provider_returns_identity_when_exists(
    db_session: AsyncSession, alice: User
) -> None:
    repo: IdentityRepository = IdentityRepository(db_session)
    await _make_password_identity(db_session, alice)

    result: Identity | None = await repo.get_by_user_and_provider(alice.id, "password")

    assert result is not None
    assert result.user_id == alice.id
    assert result.provider == "password"


async def test_get_by_user_and_provider_returns_none_when_not_found(
    db_session: AsyncSession, alice: User
) -> None:
    """Alice 沒 identity → 回 None、不 raise。"""
    repo: IdentityRepository = IdentityRepository(db_session)

    result: Identity | None = await repo.get_by_user_and_provider(alice.id, "password")

    assert result is None


async def test_get_by_user_and_provider_wrong_provider_returns_none(
    db_session: AsyncSession, alice: User
) -> None:
    """Alice 只綁 password、查 google 應該回 None。"""
    repo: IdentityRepository = IdentityRepository(db_session)
    await _make_password_identity(db_session, alice)

    result: Identity | None = await repo.get_by_user_and_provider(alice.id, "google")

    assert result is None


# ────────────────────────────────────────────────
# get_by_provider_and_sub (OAuth login 查詢)
# ────────────────────────────────────────────────
async def test_get_by_provider_and_sub_returns_identity_when_exists(
    db_session: AsyncSession, alice: User
) -> None:
    """未來 OAuth login flow:給定 Google 回傳的 sub、找對應的 identity。"""
    repo: IdentityRepository = IdentityRepository(db_session)
    await _make_oauth_identity(db_session, alice, "google", "google-sub-12345")

    result: Identity | None = await repo.get_by_provider_and_sub("google", "google-sub-12345")

    assert result is not None
    assert result.user_id == alice.id
    assert result.provider == "google"
    assert result.provider_user_id == "google-sub-12345"


async def test_get_by_provider_and_sub_returns_none_when_not_found(
    db_session: AsyncSession,
) -> None:
    """不存在的 Google sub → 回 None(用於「首次 OAuth 登入、建新 user」判斷)。"""
    repo: IdentityRepository = IdentityRepository(db_session)

    result: Identity | None = await repo.get_by_provider_and_sub("google", "nonexistent-sub")

    assert result is None


# ────────────────────────────────────────────────
# UniqueConstraint: user_id + provider
# ────────────────────────────────────────────────
async def test_uq_user_provider_prevents_duplicate_provider(
    db_session: AsyncSession, alice: User
) -> None:
    """同 user 不能建兩個 password identity(帳號綁定完整性)。"""
    await _make_password_identity(db_session, alice, credential="hash_A")

    with pytest.raises(IntegrityError):
        await _make_password_identity(db_session, alice, credential="hash_B")

    # IntegrityError 之後 session state invalid、手動 rollback 讓 fixture 能乾淨清理
    await db_session.rollback()


async def test_uq_user_provider_allows_different_providers_for_same_user(
    db_session: AsyncSession, alice: User
) -> None:
    """同 user 可以綁多個不同 provider(password + google 同時存在 = 帳號綁定)。"""
    await _make_password_identity(db_session, alice)
    await _make_oauth_identity(db_session, alice, "google", "google-sub-99")

    # Alice 現在有 2 個 identity、都應該能查到
    repo: IdentityRepository = IdentityRepository(db_session)
    password_id: Identity | None = await repo.get_by_user_and_provider(alice.id, "password")
    google_id: Identity | None = await repo.get_by_user_and_provider(alice.id, "google")

    assert password_id is not None
    assert google_id is not None


# ────────────────────────────────────────────────
# UniqueConstraint: provider + provider_user_id
# ────────────────────────────────────────────────
async def test_uq_provider_sub_prevents_hijacking(
    db_session: AsyncSession, alice: User, bob: User
) -> None:
    """同一 Google account (同 sub) 不能綁到兩個 user (防 account hijacking)。"""
    await _make_oauth_identity(db_session, alice, "google", "google-sub-shared")

    with pytest.raises(IntegrityError):
        await _make_oauth_identity(db_session, bob, "google", "google-sub-shared")

    # IntegrityError 之後 session state invalid、手動 rollback 讓 fixture 能乾淨清理
    await db_session.rollback()


# ────────────────────────────────────────────────
# Cascade delete: 刪 user 同步刪 identities
# ────────────────────────────────────────────────
async def test_cascade_delete_removes_identities(db_session: AsyncSession, alice: User) -> None:
    """刪除 User 時、其 identities 應被 DB CASCADE 一起刪(不留 orphan)。"""
    await _make_password_identity(db_session, alice)
    await _make_oauth_identity(db_session, alice, "google", "google-sub-cascade")

    # 記住 user id
    user_id: int = alice.id

    # 刪 user
    user_repo: UserRepository = UserRepository(db_session)
    await user_repo.delete(alice)
    await db_session.commit()

    # Identity 應該同步消失
    identity_repo: IdentityRepository = IdentityRepository(db_session)
    assert await identity_repo.get_by_user_and_provider(user_id, "password") is None
    assert await identity_repo.get_by_user_and_provider(user_id, "google") is None
