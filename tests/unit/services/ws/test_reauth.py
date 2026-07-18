"""WsReauthService：定期複查 is_active + session 有效性（websocket §2.2/§4）。

開短命 session 讀現值；用畢即還。涵蓋封存/刪除（is_active）與登出（session 有效性）兩類。
"""

import contextlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole
from app.models import RefreshToken
from app.services import AdminService
from app.services.ws.reauth import WsReauthService


def _factory(session: AsyncSession):
    @contextlib.asynccontextmanager
    async def _cm() -> AsyncGenerator[AsyncSession]:
        yield session  # 不 close：交回 db_session fixture 收尾

    return _cm


async def _add_refresh(
    session: AsyncSession, principal_id: int, *, family_id: str, revoked: bool = False
) -> None:
    session.add(
        RefreshToken(
            principal_id=principal_id,
            token_hash=f"h-{family_id}-{revoked}",
            family_id=family_id,
            expires_at=datetime.now(UTC) + timedelta(days=14),
            revoked_at=datetime.now(UTC) if revoked else None,
        )
    )
    await session.commit()


async def test_initial_admin_always_valid(db_session: AsyncSession) -> None:
    svc = WsReauthService(_factory(db_session))
    assert await svc.is_connection_valid(principal_id=0, sid=None, now=datetime.now(UTC)) is True


async def test_active_admin_with_live_session_valid(db_session: AsyncSession) -> None:
    admin = await AdminService(db_session).create(
        username="re-1", name="A", password="longpassword", admin_role=AdminRole.EDITOR
    )
    await _add_refresh(db_session, admin.principal_id, family_id="fam-live")
    svc = WsReauthService(_factory(db_session))

    valid = await svc.is_connection_valid(
        principal_id=admin.principal_id, sid="fam-live", now=datetime.now(UTC)
    )
    assert valid is True


async def test_archived_admin_invalid(db_session: AsyncSession) -> None:
    admin = await AdminService(db_session).create(
        username="re-2", name="A", password="longpassword", admin_role=AdminRole.EDITOR
    )
    await _add_refresh(db_session, admin.principal_id, family_id="fam-x")
    await AdminService(db_session).archive(admin.id)
    svc = WsReauthService(_factory(db_session))

    valid = await svc.is_connection_valid(
        principal_id=admin.principal_id, sid="fam-x", now=datetime.now(UTC)
    )
    assert valid is False


async def test_logged_out_session_invalid(db_session: AsyncSession) -> None:
    """admin 仍 active，但該 session 的 refresh family 已全撤（登出）→ invalid。"""
    admin = await AdminService(db_session).create(
        username="re-3", name="A", password="longpassword", admin_role=AdminRole.EDITOR
    )
    await _add_refresh(db_session, admin.principal_id, family_id="fam-out", revoked=True)
    svc = WsReauthService(_factory(db_session))

    valid = await svc.is_connection_valid(
        principal_id=admin.principal_id, sid="fam-out", now=datetime.now(UTC)
    )
    assert valid is False


async def test_no_sid_only_checks_is_active(db_session: AsyncSession) -> None:
    """無 sid（初始 admin 之外的無 session 情形）→ 只查 is_active、不查 session。"""
    admin = await AdminService(db_session).create(
        username="re-4", name="A", password="longpassword", admin_role=AdminRole.EDITOR
    )
    svc = WsReauthService(_factory(db_session))

    valid = await svc.is_connection_valid(
        principal_id=admin.principal_id, sid=None, now=datetime.now(UTC)
    )
    assert valid is True
