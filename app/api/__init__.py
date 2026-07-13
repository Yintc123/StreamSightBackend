from fastapi import APIRouter

from .routers import auth_router, health_router, users_router

# 對外的總 router
api_router: APIRouter = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)

__all__ = ["api_router"]
