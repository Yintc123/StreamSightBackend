from contextlib import asynccontextmanager
from fastapi import FastAPI
from collections.abc import AsyncGenerator

from .api import api_router
from .api.middlewares import RequestIdMiddleware
from .core.config import get_app_settings
from .core.db import engine
from .core.exceptions import setup_exception_handlers
from .core.logging import setup_logging

# asynccontextmanager: 在 async 環境下,安全地管理「需要 setup + teardown」的資源,而且它的進入 / 離開動作本身可以 await。
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    App lifespan: startup / shutdown hooks.
    startup 執行一次 (yield 之前)，shutdown 執行一次 (yield 之後)
    目前僅用於當 shutdown 時清除 DB connection。
    - `app`參數： FastAPI 傳入，讓 handler 可存取 app.state (目前未用到)
    - startup：engine 已在 import 時建立，無需初始化 DB
    """
    # startup: 目前無程式碼需要執行 (engine 已在 import 時建立)
    yield
    # shutdown: 關掉所有 DB connection
    await engine.dispose()

def create_app() -> FastAPI:
    setup_logging()
    app_settings = get_app_settings()

    app: FastAPI = FastAPI(
        title = app_settings.app_name,
        version = app_settings.app_version,
        debug = app_settings.app_debug,
        lifespan=lifespan,
    )

    setup_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(api_router)

    return app
