"""Unit tests：access token 的 `sid` claim 串接（websocket §2.11 前置）。

sid = 該登入的 refresh family_id。login/admin_login/register 帶當次 family_id；
refresh rotation 保持同一 family_id → 同一 session 跨多次 refresh 的 sid 不變。
初始 admin（sub=0）走 access-only、無 refresh family → 無 sid。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token, extract_sid, hash_refresh_token
from app.dtos import (
    AdminLoginRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
)
from app.models import Admin
from app.repositories import RefreshTokenRepository
from app.services import AuthService


async def test_register_access_token_sid_matches_refresh_family(db_session: AsyncSession) -> None:
    auth = AuthService(db_session)
    token = await auth.register(
        RegisterRequest(email="sid@example.com", name="U", password="longpassword")
    )
    assert token.refresh_token is not None

    repo = RefreshTokenRepository(db_session)
    row = await repo.get_by_hash(hash_refresh_token(token.refresh_token))
    assert row is not None

    sid = extract_sid(decode_token(token.access_token))
    assert sid == row.family_id


async def test_login_access_token_sid_matches_refresh_family(db_session: AsyncSession) -> None:
    auth = AuthService(db_session)
    await auth.register(RegisterRequest(email="lo@example.com", name="U", password="longpassword"))

    token = await auth.login(LoginRequest(email="lo@example.com", password="longpassword"))
    assert token.refresh_token is not None

    repo = RefreshTokenRepository(db_session)
    row = await repo.get_by_hash(hash_refresh_token(token.refresh_token))
    assert row is not None
    assert extract_sid(decode_token(token.access_token)) == row.family_id


async def test_refresh_keeps_stable_sid_across_rotation(db_session: AsyncSession) -> None:
    """同一 session 跨多次 refresh 的 sid 不變（= 穩定 session 識別）。"""
    auth = AuthService(db_session)
    first = await auth.register(
        RegisterRequest(email="rot@example.com", name="U", password="longpassword")
    )
    assert first.refresh_token is not None
    sid_before = extract_sid(decode_token(first.access_token))

    rotated = await auth.refresh(RefreshRequest(refresh_token=first.refresh_token))
    sid_after = extract_sid(decode_token(rotated.access_token))

    assert sid_before is not None
    assert sid_after == sid_before


async def test_admin_login_access_token_has_sid(db_session: AsyncSession, admin: Admin) -> None:
    from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

    auth = AuthService(db_session)
    token = await auth.admin_login(
        AdminLoginRequest(username=ADMIN_USERNAME, password=ADMIN_PASSWORD)
    )
    assert token.refresh_token is not None

    repo = RefreshTokenRepository(db_session)
    row = await repo.get_by_hash(hash_refresh_token(token.refresh_token))
    assert row is not None
    assert extract_sid(decode_token(token.access_token)) == row.family_id
