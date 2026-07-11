import logging
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.config import BaseAppSettings, get_app_settings
from app.core.context import request_id_ctx

from .base import AppException, SystemErrorCode

# 讓 log 能印出模組名稱
logger: logging.Logger = logging.getLogger(__name__)


def _build_response(
    status_code: int,
    error_code: str,
    message: str,
    details: dict[str, Any] | None = None,
    debug_info: dict[str, Any] | None = None,
) -> JSONResponse:
    """建立 request_id 標準化的 JSON 格式的錯誤回應"""
    content: dict[str, Any] = {
        "error": error_code,
        "message": message,
        "request_id": request_id_ctx.get(),
    }

    if details:
        content["details"] = details
    if debug_info:
        content["debug_info"] = debug_info

    return JSONResponse(status_code=status_code, content=content)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """處理 AppException 和他的子類別"""
    # 5xx 用 error level + traceback，4xx 用 warning level
    if exc.status_code >= 500:
        # exc_info 為 True 會完整印出 exception 訊息(包含 traceback)
        logger.error("AppException: %s", exc.message, exc_info=True)
    else:
        logger.warning("AppException: %s (%s)", exc.message, exc.error_code)

    return _build_response(
        status_code=exc.status_code,
        error_code=exc.error_code,
        message=exc.message or "An error occurred",
        details=exc.details or None,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """取代 FastAPI 預設的 HTTPException handler 並且使用標準化的 JSON 格式回應"""
    return _build_response(
        status_code=exc.status_code, error_code=SystemErrorCode.HTTP_ERROR, message=str(exc.detail)
    )


# 處理 Pydantic 驗證請求失敗的 error handler
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic/FastAPI 請求驗證錯誤 -> 標準化 JSON 格式"""
    return _build_response(
        status_code=422,
        error_code=SystemErrorCode.VALIDATION_ERROR,
        message="Request validation failed",
        details={"errors": exc.errors()},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """非預期錯誤使用並且記錄全部的 traceback"""
    logger.exception("Unhandled exception: %s", exc)

    settings: BaseAppSettings = get_app_settings()
    debug_info: dict[str, Any] | None = None
    safe_message: str = "An unexpected error occurred"

    # debug=True 時回傳詳細資訊 (dev / local 看exception 觸發時的完整呼叫鏈(stacktrace))
    # debug=False 時隱藏細節 (prod 要設置 False，避免洩漏機密資訊)
    if settings.app_debug:
        debug_info = {
            "exception_type": type(exc).__name__,
            "traceback": traceback.format_exception(type(exc), exc, exc.__traceback__),
        }
        safe_message = str(exc)

    return _build_response(
        status_code=500,
        error_code=SystemErrorCode.INTERNAL_ERROR,
        message=safe_message,
        debug_info=debug_info,
    )


def setup_exception_handlers(app: FastAPI) -> None:
    """FastAPI app 註冊所有 exception handlers.

    FastAPI 的 `add_exception_handler` 型別要求 handler 第二參數為 `Exception`（最寬型別），
    但刻意用更窄的型別（`AppException` / `RequestValidationError` / `HTTPException`）以獲得
    handler 內部的型別支援。runtime 完全正確 — starlette 只會用對應的 exception 傳進去。
    """
    app.add_exception_handler(AppException, app_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(Exception, unhandled_exception_handler)
