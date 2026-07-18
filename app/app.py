from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import api_router
from .api.middlewares import RequestIdMiddleware
from .core.config import BaseAppSettings, get_app_settings
from .core.db import engine
from .core.exceptions import setup_exception_handlers
from .core.logging import setup_logging
from .core.redis import close_redis, redis_client
from .services.ws import ConnectionManager
from .services.ws.bridge import WsBridge
from .services.ws.protocol import WSCloseCode


# asynccontextmanager: 在 async 環境下,安全地管理「需要 setup + teardown」的資源,而且它的進入 / 離開動作本身可以 await。
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """
    App lifespan: startup / shutdown hooks.
    startup 執行一次 (yield 之前)，shutdown 執行一次 (yield 之後)
    目前僅用於當 shutdown 時清除 DB connection。
    - `app`參數： FastAPI 傳入，讓 handler 可存取 app.state (目前未用到)
    - startup：engine 已在 import 時建立，無需初始化 DB
    """
    # startup：啟動 WS bridge 背景 task（訂閱 Redis pub/sub → 投遞/kick，websocket §2.4）。
    bridge: WsBridge = WsBridge(redis_client, app.state.ws_manager)
    await bridge.start()
    app.state.ws_bridge = bridge
    yield
    # shutdown: 先對本實例所有 WS 連線送 close(1012) 優雅斷線（websocket §2.2/§3.4），
    # 再收斂 bridge，最後關掉所有 DB / Redis connection。
    await app.state.ws_manager.close_all(WSCloseCode.SERVICE_RESTART)
    await bridge.stop()
    await engine.dispose()
    await close_redis()


def create_app() -> FastAPI:
    setup_logging()
    app_settings: BaseAppSettings = get_app_settings()

    app: FastAPI = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        debug=app_settings.app_debug,
        lifespan=lifespan,
    )

    setup_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    # per-process WS 連線註冊表（app.state 單例；於 create_app 建立，不依賴 lifespan，
    # 讓測試的 ASGITransport（不跑 lifespan）也能取用，見 websocket §2.3/§4）。
    app.state.ws_manager = ConnectionManager()

    app.include_router(api_router)

    return app
