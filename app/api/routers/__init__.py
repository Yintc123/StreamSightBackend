from .admin import router as admin_router
from .auth import router as auth_router
from .health import router as health_router
from .monitoring import router as monitoring_router
from .realtime import router as realtime_router
from .records import router as records_router
from .users import router as users_router
from .ws import router as ws_router

__all__ = [
    "admin_router",
    "auth_router",
    "health_router",
    "monitoring_router",
    "realtime_router",
    "records_router",
    "users_router",
    "ws_router",
]
