from fastapi import FastAPI

from .api import api_router
from .api.middlewares import RequestIdMiddleware
from .core.config import get_app_settings
from .core.exceptions import setup_exception_handlers
from .core.logging import setup_logging

def create_app() -> FastAPI:
    setup_logging()
    app_settings = get_app_settings()

    app: FastAPI = FastAPI(
        title = app_settings.app_name,
        version = app_settings.app_version,
        debug = app_settings.app_debug
    )

    setup_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(api_router)

    return app
