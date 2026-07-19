"""WS 單元測試共用：FakeWebSocket + Connection 工廠。"""

from __future__ import annotations

import asyncio
from typing import cast

from app.services.ws.manager import Connection


class FakeWebSocket:
    """最小 WebSocket 替身：記錄送出/關閉，可設定 send 時拋指定例外。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed_code: int | None = None
        self.raise_on_send: BaseException | None = None

    async def send_json(self, data: dict) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        if self.closed_code is None:
            self.closed_code = code


def fake_ws(conn: Connection) -> FakeWebSocket:
    """型別安全地取回連線底層的 FakeWebSocket（Connection.ws 標註為 WebSocket）。"""
    return cast(FakeWebSocket, conn.ws)


def make_connection(
    *,
    principal_id: int = 1,
    admin_role: int = 50,
    sid: str | None = "sess-1",
    cid: str | None = "tab-1",
    max_queue: int = 100,
    ws: FakeWebSocket | None = None,
) -> Connection:
    return Connection(
        ws=ws or FakeWebSocket(),  # type: ignore[arg-type]
        principal_id=principal_id,
        admin_role=admin_role,
        sid=sid,
        cid=cid,
        is_active=True,
        queue=asyncio.Queue(maxsize=max_queue),
    )
