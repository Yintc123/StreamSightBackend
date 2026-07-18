"""Lifespan shutdown → 對本實例所有 WS 連線送 close(1012)（websocket §2.2/§3.4）。

實例關閉時應優雅斷線（Service restart, 1012），而非讓連線被動被丟棄。
以 FakeBridge 取代真實 WsBridge（避免 lifespan 啟動時連真實 Redis）。
"""

from app.app import create_app, lifespan
from app.services.ws.protocol import WSCloseCode
from tests.unit.services.ws.conftest import fake_ws, make_connection


class _FakeBridge:
    """no-op bridge：讓 lifespan startup/shutdown 不碰真實 Redis。"""

    def __init__(self, *args: object, **kwargs: object) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


async def test_lifespan_shutdown_closes_connections_1012(monkeypatch) -> None:
    monkeypatch.setattr("app.app.WsBridge", _FakeBridge)
    app = create_app()
    conn = make_connection(principal_id=1, sid="s", cid="c")

    async with lifespan(app):
        await app.state.ws_manager.register(conn)
    # 離開 lifespan（shutdown）後：連線被以 1012 優雅關閉、索引清空。
    assert fake_ws(conn).closed_code == WSCloseCode.SERVICE_RESTART  # 1012
    assert conn.closed is True
    assert app.state.ws_manager.total_connections == 0
