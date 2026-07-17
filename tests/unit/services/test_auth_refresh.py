"""Unit tests for AuthService refresh-token flows. Spec §8.3.

覆蓋 register/login 發 refresh、rotation、reuse detection（含 grace）、
user 停用、opportunistic 清理、logout / logout_all。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_refresh_token
from app.core.exceptions import UnauthorizedError
from app.dtos import RefreshRequest, RegisterRequest
from app.models import RefreshToken, User
from app.repositories import RefreshTokenRepository
from app.services import AuthService


async def _register(auth: AuthService, email: str = "u@example.com") -> str:
    """Register a user and return the issued refresh token (plaintext)."""
    token = await auth.register(RegisterRequest(email=email, name="U", password="longpassword"))
    assert token.refresh_token is not None
    return token.refresh_token


async def _store_token(
    session: AsyncSession,
    user: User,
    plaintext: str,
    *,
    family_id: str = "F",
    expires_delta: timedelta = timedelta(days=14),
    revoked_at: datetime | None = None,
) -> RefreshToken:
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(plaintext),
        family_id=family_id,
        expires_at=datetime.now(UTC) + expires_delta,
        revoked_at=revoked_at,
    )
    session.add(rt)
    await session.commit()
    await session.refresh(rt)
    return rt


# ── register / login 發 refresh ─────────────────────────────
async def test_register_issues_active_refresh_token(db_session: AsyncSession) -> None:
    auth = AuthService(db_session)
    plaintext = await _register(auth)

    repo = RefreshTokenRepository(db_session)
    row = await repo.get_by_hash(hash_refresh_token(plaintext))
    assert row is not None
    assert row.revoked_at is None


# ── refresh：rotation ───────────────────────────────────────
async def test_refresh_rotates_and_revokes_old(db_session: AsyncSession) -> None:
    auth = AuthService(db_session)
    old_plain = await _register(auth)
    repo = RefreshTokenRepository(db_session)
    old_row = await repo.get_by_hash(hash_refresh_token(old_plain))
    assert old_row is not None

    result = await auth.refresh(RefreshRequest(refresh_token=old_plain))

    assert result.refresh_token is not None
    assert result.refresh_token != old_plain
    new_row = await repo.get_by_hash(hash_refresh_token(result.refresh_token))
    assert new_row is not None
    # 舊 token 撤銷並指向新 token；新舊同 family
    await db_session.refresh(old_row)
    assert old_row.revoked_at is not None
    assert old_row.replaced_by_id == new_row.id
    assert new_row.family_id == old_row.family_id


async def test_refresh_unknown_token_raises(db_session: AsyncSession) -> None:
    auth = AuthService(db_session)
    with pytest.raises(UnauthorizedError):
        await auth.refresh(RefreshRequest(refresh_token="not-a-real-token"))


async def test_refresh_expired_token_raises(db_session: AsyncSession, alice: User) -> None:
    auth = AuthService(db_session)
    await _store_token(db_session, alice, "expired-tok", expires_delta=timedelta(seconds=-1))

    with pytest.raises(UnauthorizedError, match="expired"):
        await auth.refresh(RefreshRequest(refresh_token="expired-tok"))


async def test_refresh_reused_token_beyond_grace_nukes_family(
    db_session: AsyncSession, alice: User
) -> None:
    auth = AuthService(db_session)
    # 舊 token 已撤銷且超過 grace；同 family 另有一個 active token
    await _store_token(
        db_session,
        alice,
        "old-revoked",
        family_id="FAM",
        revoked_at=datetime.now(UTC) - timedelta(seconds=60),
    )
    sibling = await _store_token(db_session, alice, "sibling-active", family_id="FAM")

    with pytest.raises(UnauthorizedError):
        await auth.refresh(RefreshRequest(refresh_token="old-revoked"))

    # reuse detection：整條 family 連坐
    await db_session.refresh(sibling)
    assert sibling.revoked_at is not None


async def test_refresh_reused_token_within_grace_does_not_nuke_family(
    db_session: AsyncSession, alice: User
) -> None:
    auth = AuthService(db_session)
    await _store_token(
        db_session,
        alice,
        "just-rotated",
        family_id="FAM",
        revoked_at=datetime.now(UTC),  # grace 內
    )
    sibling = await _store_token(db_session, alice, "sibling-active", family_id="FAM")

    with pytest.raises(UnauthorizedError):
        await auth.refresh(RefreshRequest(refresh_token="just-rotated"))

    # grace 良性路徑：family 其餘 token 不受影響
    await db_session.refresh(sibling)
    assert sibling.revoked_at is None


async def test_refresh_inactive_user_raises(db_session: AsyncSession) -> None:
    auth = AuthService(db_session)
    plaintext = await _register(auth, email="inactive@example.com")
    user = await auth.user_service.repo.get_by_email("inactive@example.com")
    assert user is not None
    user.is_active = False
    await db_session.commit()

    with pytest.raises(UnauthorizedError):
        await auth.refresh(RefreshRequest(refresh_token=plaintext))


# ── login opportunistic 清理 ────────────────────────────────
async def test_login_purges_expired_tokens(db_session: AsyncSession) -> None:
    from app.dtos import LoginRequest

    auth = AuthService(db_session)
    await auth.register(
        RegisterRequest(email="cleanup@example.com", name="C", password="longpassword")
    )
    user = await auth.user_service.repo.get_by_email("cleanup@example.com")
    assert user is not None
    await _store_token(db_session, user, "stale", expires_delta=timedelta(seconds=-1))

    await auth.login(LoginRequest(email="cleanup@example.com", password="longpassword"))

    repo = RefreshTokenRepository(db_session)
    assert await repo.get_by_hash(hash_refresh_token("stale")) is None


# ── logout / logout_all ─────────────────────────────────────
async def test_logout_revokes_token(db_session: AsyncSession) -> None:
    auth = AuthService(db_session)
    plaintext = await _register(auth)

    await auth.logout(RefreshRequest(refresh_token=plaintext))

    repo = RefreshTokenRepository(db_session)
    row = await repo.get_by_hash(hash_refresh_token(plaintext))
    assert row is not None
    assert row.revoked_at is not None
    # 撤銷後不可再 refresh
    with pytest.raises(UnauthorizedError):
        await auth.refresh(RefreshRequest(refresh_token=plaintext))


async def test_logout_unknown_token_is_silent(db_session: AsyncSession) -> None:
    auth = AuthService(db_session)
    # 不拋錯
    await auth.logout(RefreshRequest(refresh_token="never-existed"))


async def test_logout_all_revokes_only_target_user(
    db_session: AsyncSession, alice: User, bob: User
) -> None:
    auth = AuthService(db_session)
    a1 = await _store_token(db_session, alice, "a1", family_id="A1")
    a2 = await _store_token(db_session, alice, "a2", family_id="A2")
    b1 = await _store_token(db_session, bob, "b1", family_id="B1")

    await auth.logout_all(alice.id)

    for t in (a1, a2, b1):
        await db_session.refresh(t)
    assert a1.revoked_at is not None
    assert a2.revoked_at is not None
    assert b1.revoked_at is None
