from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import BaseAppSettings, get_app_settings
from app.core.db import get_session
from app.core.exceptions import BusinessRuleError, NotFoundError

from .schemas import (
    ErrorResponse,
    HealthDbResponse,
    HealthResponse,
    TestErrorResponse,
)

router: APIRouter = APIRouter()


@router.get("/health")
def health(settings: BaseAppSettings = Depends(get_app_settings)) -> HealthResponse:
    return HealthResponse(message="ok", app_version=settings.app_version)


@router.get(
    "/health/test-error/{kind}",
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


@router.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_session)) -> HealthDbResponse:
    result: Result[tuple[int]] = await db.execute(text("SELECT 1"))
    return HealthDbResponse(db="ok", result=result.scalar_one())
