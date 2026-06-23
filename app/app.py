from fastapi import FastAPI

from .api import api_router
from .core.config import get_app_settings
from .core.logging import setup_logging

def create_app() -> FastAPI:
    setup_logging()
    app_settings = get_app_settings()

    app: FastAPI = FastAPI(
        title = app_settings.app_name,
        version = app_settings.app_version,
        debug = app_settings.app_debug
    )

    app.include_router(api_router)

    return app
