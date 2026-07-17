"""Unit tests for RefreshTokenRepository. Spec §8.2.

覆蓋：
    - get_by_hash lookup
    - consume（原子式消費：active→True 並撤銷/設 replaced_by；已撤銷→False 不覆寫）
    - revoke_family / revoke_all_for_user（只撤 active、回正確筆數、不越界）
    - delete_expired（只刪過期、回筆數；含自參考 FK 鏈的批次刪除不報 FK 違規）
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User
from app.repositories import RefreshTokenRepository


async def _make_token(
    session: AsyncSession,
    user: User,
    *,
    token_hash: str,
    family_id: str = "fam-1",
    expires_delta: timedelta = timedelta(days=14),
    revoked_at: datetime | None = None,
    replaced_by_id: int | None = None,
) -> RefreshToken:
    rt: RefreshToken = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        family_id=family_id,
        expires_at=datetime.now(UTC) + expires_delta,
        revoked_at=revoked_at,
        replaced_by_id=replaced_by_id,
    )
    session.add(rt)
    await session.commit()
    await session.refresh(rt)
    return rt


# ── get_by_hash ─────────────────────────────────────────────
async def test_get_by_hash_returns_token(db_session: AsyncSession, alice: User) -> None:
    repo = RefreshTokenRepository(db_session)
    await _make_token(db_session, alice, token_hash="hash-a")

    result = await repo.get_by_hash("hash-a")

    assert result is not None
    assert result.token_hash == "hash-a"


async def test_get_by_hash_returns_none_when_missing(db_session: AsyncSession, alice: User) -> None:
    repo = RefreshTokenRepository(db_session)
    assert await repo.get_by_hash("nope") is None


# ── consume（原子式消費）────────────────────────────────────
async def test_consume_active_returns_true_and_revokes(
    db_session: AsyncSession, alice: User
) -> None:
    repo = RefreshTokenRepository(db_session)
    new_token = await _make_token(db_session, alice, token_hash="new")
    old_token = await _make_token(db_session, alice, token_hash="old")

    now = datetime.now(UTC)
    won = await repo.consume(old_token.id, revoked_at=now, replaced_by_id=new_token.id)

    assert won is True
    await db_session.refresh(old_token)
    assert old_token.revoked_at is not None
    assert old_token.replaced_by_id == new_token.id


async def test_consume_already_revoked_returns_false_no_overwrite(
    db_session: AsyncSession, alice: User
) -> None:
    repo = RefreshTokenRepository(db_session)
    revoked = await _make_token(
        db_session, alice, token_hash="revoked", revoked_at=datetime.now(UTC)
    )

    won = await repo.consume(revoked.id, revoked_at=datetime.now(UTC), replaced_by_id=999)

    assert won is False
    await db_session.refresh(revoked)
    # 未被覆寫（原子性：第二個並發請求搶不到）
    assert revoked.replaced_by_id is None


# ── revoke_family ───────────────────────────────────────────
async def test_revoke_family_only_revokes_matching_active(
    db_session: AsyncSession, alice: User
) -> None:
    repo = RefreshTokenRepository(db_session)
    f1a = await _make_token(db_session, alice, token_hash="f1a", family_id="F1")
    f1b = await _make_token(db_session, alice, token_hash="f1b", family_id="F1")
    f2 = await _make_token(db_session, alice, token_hash="f2", family_id="F2")

    count = await repo.revoke_family("F1", datetime.now(UTC))

    assert count == 2
    for t in (f1a, f1b, f2):
        await db_session.refresh(t)
    assert f1a.revoked_at is not None
    assert f1b.revoked_at is not None
    assert f2.revoked_at is None  # 其他 family 不受影響


# ── revoke_all_for_user ─────────────────────────────────────
async def test_revoke_all_for_user_only_active_and_scoped(
    db_session: AsyncSession, alice: User, bob: User
) -> None:
    repo = RefreshTokenRepository(db_session)
    a_active = await _make_token(db_session, alice, token_hash="a-active")
    a_revoked = await _make_token(
        db_session, alice, token_hash="a-revoked", revoked_at=datetime.now(UTC)
    )
    b_active = await _make_token(db_session, bob, token_hash="b-active")

    count = await repo.revoke_all_for_user(alice.id, datetime.now(UTC))

    assert count == 1  # 只有 a_active（a_revoked 已撤銷不算）
    for t in (a_active, a_revoked, b_active):
        await db_session.refresh(t)
    assert a_active.revoked_at is not None
    assert b_active.revoked_at is None  # 其他 user 不受影響


# ── delete_expired ──────────────────────────────────────────
async def test_delete_expired_only_removes_expired(db_session: AsyncSession, alice: User) -> None:
    repo = RefreshTokenRepository(db_session)
    await _make_token(db_session, alice, token_hash="expired", expires_delta=timedelta(seconds=-1))
    await _make_token(db_session, alice, token_hash="valid", expires_delta=timedelta(days=1))

    count = await repo.delete_expired(datetime.now(UTC))

    assert count == 1
    assert await repo.get_by_hash("expired") is None
    assert await repo.get_by_hash("valid") is not None


async def test_delete_expired_handles_self_referential_chain(
    db_session: AsyncSession, alice: User
) -> None:
    """OLD→NEW（replaced_by_id 互參考）皆過期，批次刪除不報 FK 違規。"""
    repo = RefreshTokenRepository(db_session)
    new_token = await _make_token(
        db_session, alice, token_hash="chain-new", expires_delta=timedelta(seconds=-1)
    )
    await _make_token(
        db_session,
        alice,
        token_hash="chain-old",
        expires_delta=timedelta(seconds=-2),
        replaced_by_id=new_token.id,
    )

    count = await repo.delete_expired(datetime.now(UTC))

    assert count == 2
    assert await repo.get_by_hash("chain-old") is None
    assert await repo.get_by_hash("chain-new") is None
