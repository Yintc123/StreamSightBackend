"""POST /admin/ws/ticket：JWT 換短命單次 ticket（websocket §2.1/§7.0）。

只 admin（role=1、active）能換票；ticket 綁 principal_id + sid（= 當次 access token sid）。
"""

import redis.asyncio as redis
from fastapi import status
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token, extract_sid
from app.core.config import get_app_settings
from app.models import Admin
from app.services import AdminService
from app.services.ws.ticket import TicketService
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _admin_access(client: AsyncClient) -> str:
    resp = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_ticket_endpoint_returns_ticket_and_expiry(client: AsyncClient, admin: Admin) -> None:
    access = await _admin_access(client)

    resp: Response = await client.post("/admin/ws/ticket", headers=_auth(access))

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["ticket"]
    assert body["expires_in"] == get_app_settings().ws_ticket_ttl_seconds


async def test_ticket_binds_principal_and_sid(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """ticket 消費後回 (admin.principal_id, sid=token 的 sid)。"""
    access = await _admin_access(client)
    expected_sid = extract_sid(decode_token(access))

    resp = await client.post("/admin/ws/ticket", headers=_auth(access))
    ticket = resp.json()["ticket"]

    consumed = await TicketService(fake_redis).consume(ticket)
    assert consumed == (admin.principal_id, expected_sid)


async def test_ticket_requires_auth(client: AsyncClient, admin: Admin) -> None:
    resp = await client.post("/admin/ws/ticket")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_ticket_rejects_user_token(client: AsyncClient) -> None:
    """role=0 的 user token → 403（get_current_admin）。"""
    reg = await client.post(
        "/auth/register", json={"email": "u@example.com", "name": "U", "password": "longpassword"}
    )
    user_access = reg.json()["access_token"]

    resp = await client.post("/admin/ws/ticket", headers=_auth(user_access))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_ticket_rejects_archived_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    """簽發 token 後帳號被封存 → 重載見 inactive → 401。"""
    from app.core.enums import AdminRole

    editor = await AdminService(db_session).create(
        username="editor-ws", name="E", password="longpassword", admin_role=AdminRole.EDITOR
    )
    login = await client.post(
        "/admin/auth/login", json={"username": "editor-ws", "password": "longpassword"}
    )
    access = login.json()["access_token"]

    await AdminService(db_session).archive(editor.id)

    resp = await client.post("/admin/ws/ticket", headers=_auth(access))
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
