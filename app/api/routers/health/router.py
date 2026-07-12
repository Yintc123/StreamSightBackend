import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_redis, get_session
from app.core.config import BaseAppSettings, get_app_settings
from app.core.exceptions import BusinessRuleError, NotFoundError

from .schemas import (
    ErrorResponse,
    HealthDbResponse,
    HealthRedisResponse,
    HealthResponse,
    TestErrorResponse,
)

router: APIRouter = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(settings: BaseAppSettings = Depends(get_app_settings)) -> HealthResponse:
    return HealthResponse(message="ok", app_version=settings.app_version)


@router.get(
    "/test-error/{kind}",
    response_model=TestErrorResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Resource not found"},
        422: {"model": ErrorResponse, "description": "Business rule violation"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def test_error(kind: str) -> TestErrorResponse:
    """
    DEMO endpoint for testing exception handlers.

    Kinds: notfound (404), business (422), unhandled (500), other (200)
    Remove before deploying to production.
    """
    if kind == "notfound":
        raise NotFoundError("Test resource not found")
    if kind == "business":
        raise BusinessRuleError("Test business rule violation", details={"field": "test"})
    if kind == "unhandled":
        raise RuntimeError("Test unhandled error")
    return TestErrorResponse(status="no error")


@router.get("/db")
async def health_db(db: AsyncSession = Depends(get_session)) -> HealthDbResponse:
    result: Result[tuple[int]] = await db.execute(text("SELECT 1"))
    return HealthDbResponse(db="ok", result=result.scalar_one())


@router.get("/redis")
async def health_redis(redis: redis.Redis = Depends(get_redis)) -> HealthRedisResponse:
    """
    Return Redis connectivity by executing PING.

    - Redis 可用：回 200，ping = True
    - Redis 不可用：ConnectionError -> unhandled_exception_handler -> 500
    """
    pong: bool = await redis.ping()
    return HealthRedisResponse(redis="ok", ping=pong)
