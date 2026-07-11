from fastapi import APIRouter

from .routers import users_router
from .routers.health import router as health_router

# 對外的總 router
api_router: APIRouter = APIRouter()
api_router.include_router(health_router)
api_router.include_router(users_router, prefix="/api/v1")

__all__ = ["api_router"]
