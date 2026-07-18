from fastapi import APIRouter

from .routers import (
    admin_router,
    auth_router,
    health_router,
    monitoring_router,
    users_router,
    ws_router,
)

# 對外的總 router
api_router: APIRouter = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(admin_router)
api_router.include_router(ws_router)
api_router.include_router(monitoring_router)

__all__ = ["api_router"]
