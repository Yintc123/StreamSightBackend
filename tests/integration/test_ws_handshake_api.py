"""WS handshake 兩段式認證（websocket §3.1/§7.1）。

accept 前 GETDEL 驗票（單次、防重放）→ 重載 Admin 讀現值（is_active）→ accept + welcome；
失敗一律 accept 後 close(4401)（spike 陷阱 #2：4xxx close code 只在 upgrade 成功後有效）。
"""

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole
from app.services import AdminService
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def _extract_close_code(exc: BaseException) -> int | None:
    """從（可能被 anyio 包成 ExceptionGroup 的）例外挖出 WS close code（spike 陷阱 #1）。"""
    if isinstance(exc, WebSocketDisconnect):
        return exc.code
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            code = _extract_close_code(sub)
            if code is not None:
                return code
    return None


async def _ticket(
    client: AsyncClient, username: str = ADMIN_USERNAME, password: str = ADMIN_PASSWORD
) -> str:
    login = await client.post(
        "/admin/auth/login", json={"username": username, "password": password}
    )
    access = login.json()["access_token"]
    resp = await client.post("/ws/ticket", headers={"Authorization": f"Bearer {access}"})
    return resp.json()["ticket"]


async def _expect_close_code(ws_client: AsyncClient, url: str) -> int:
    try:
        async with aconnect_ws(url, ws_client) as ws:
            await ws.receive_json()
        raise AssertionError("expected the server to close the connection")
    except WebSocketDisconnect as e:
        return e.code
    except BaseExceptionGroup as eg:
        code = _extract_close_code(eg)
        if code is None:
            raise
        return code


# ── 拒絕類（accept 後 close 4401）───────────────────────────────
async def test_ws_no_ticket_closes_4401(ws_client: AsyncClient, admin) -> None:
    assert await _expect_close_code(ws_client, "http://test/ws") == 4401


async def test_ws_garbage_ticket_closes_4401(ws_client: AsyncClient, admin) -> None:
    assert await _expect_close_code(ws_client, "http://test/ws?ticket=not-real") == 4401


async def test_ws_used_ticket_closes_4401(ws_client: AsyncClient, admin) -> None:
    """單次：ticket 用過一次後再連 → 4401（防重放）。"""
    ticket = await _ticket(ws_client)
    async with aconnect_ws(f"http://test/ws?ticket={ticket}", ws_client) as ws:
        assert (await ws.receive_json())["type"] == "welcome"
    # 第二次用同一 ticket
    assert await _expect_close_code(ws_client, f"http://test/ws?ticket={ticket}") == 4401


# ── 成功類（accept + welcome）───────────────────────────────────
async def test_ws_valid_ticket_accepts_and_welcomes(ws_client: AsyncClient, admin) -> None:
    ticket = await _ticket(ws_client)
    async with aconnect_ws(f"http://test/ws?ticket={ticket}", ws_client) as ws:
        welcome = await ws.receive_json()
    assert welcome["type"] == "welcome"
    assert welcome["admin_role"] == "super_admin"
    assert welcome["connection_id"]


async def test_ws_editor_can_connect(ws_client: AsyncClient, db_session: AsyncSession) -> None:
    await AdminService(db_session).create(
        username="ws-editor", name="E", password="longpassword", admin_role=AdminRole.EDITOR
    )
    ticket = await _ticket(ws_client, username="ws-editor", password="longpassword")
    async with aconnect_ws(f"http://test/ws?ticket={ticket}", ws_client) as ws:
        welcome = await ws.receive_json()
    assert welcome["admin_role"] == "editor"


async def test_ws_archived_after_ticket_closes_4401(
    ws_client: AsyncClient, db_session: AsyncSession
) -> None:
    """簽發 ticket 後帳號被封存 → 消費後重載見 inactive → 4401（消費後讀現值）。"""
    editor = await AdminService(db_session).create(
        username="ws-arch", name="E", password="longpassword", admin_role=AdminRole.EDITOR
    )
    ticket = await _ticket(ws_client, username="ws-arch", password="longpassword")
    await AdminService(db_session).archive(editor.id)

    assert await _expect_close_code(ws_client, f"http://test/ws?ticket={ticket}") == 4401


async def test_ws_initial_admin_can_connect(
    ws_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """初始 admin（sub=0）換 ticket → 亦可連（合成 super_admin）。"""
    from pydantic import SecretStr

    from app.core.auth import hash_password
    from app.core.config import get_app_settings

    settings = get_app_settings()
    pw_hash = await hash_password("initial-longpassword")
    monkeypatch.setattr(settings, "initial_admin_username", "root-init-ws")
    monkeypatch.setattr(settings, "initial_admin_password_hash", SecretStr(pw_hash))

    ticket = await _ticket(ws_client, username="root-init-ws", password="initial-longpassword")
    async with aconnect_ws(f"http://test/ws?ticket={ticket}", ws_client) as ws:
        welcome = await ws.receive_json()
    assert welcome["admin_role"] == "super_admin"
