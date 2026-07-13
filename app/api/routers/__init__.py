from .auth import router as auth_router
from .health import router as health_router
from .users import router as users_router

__all__ = [
    "health_router",
    "users_router",
    "auth_router",
]
