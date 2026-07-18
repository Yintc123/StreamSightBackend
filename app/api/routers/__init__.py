from .admin import router as admin_router
from .admin import ws_router as admin_ws_router
from .auth import router as auth_router
from .health import router as health_router
from .users import router as users_router

__all__ = [
    "admin_router",
    "admin_ws_router",
    "health_router",
    "users_router",
    "auth_router",
]
