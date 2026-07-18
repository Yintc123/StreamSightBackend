from enum import StrEnum
from typing import Any


class SystemErrorCode(StrEnum):
    """Framework 層 error codes（沒有對應 AppException 子類別的錯誤）。

    - HTTP_ERROR：Starlette/FastAPI 原生 HTTPException
    - VALIDATION_ERROR：Pydantic/FastAPI 請求驗證失敗
    - INTERNAL_ERROR：非預期的未捕獲 exception（也作為 AppException 預設值）
    """

    HTTP_ERROR = "http_error"
    VALIDATION_ERROR = "validation_error"
    INTERNAL_ERROR = "internal_error"


# AppException 屬於業務層；HTTPException 屬於 framework 層
class AppException(Exception):
    """
    全部業務層的 exceptions 都從這個 class 繼承，
    子類別設置 status_code 和 error_code 作為子類別的屬性
    """

    status_code: int = 500
    error_code: str = SystemErrorCode.INTERNAL_ERROR

    # * 強制之後的參數帶上 keyword
    # raise NotFoundError("user", {"id": 1}) -> TypeError，強制要求 keyword
    # raise NotFoundError("user", details={"id": 1}) -> 正確
    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        # 將 message 傳給 Exception 的 args，讓 traceback 和 log 可以追蹤
        super().__init__(message)
        self.message: str = message
        self.details: dict[str, Any] = details or {}


class NotFoundError(AppException):
    status_code: int = 404
    error_code: str = "not_found"


class UnauthorizedError(AppException):
    status_code: int = 401
    error_code: str = "unauthorized"


class ForbiddenError(AppException):
    status_code: int = 403
    error_code: str = "forbidden"


class ConflictError(AppException):
    status_code: int = 409
    error_code: str = "conflict"


class BadRequestError(AppException):
    status_code: int = 400
    error_code: str = "bad_request"


class BusinessRuleError(AppException):
    """
    請求有效但違反商業邏輯
    """

    status_code: int = 422
    error_code: str = "business_rule_violation"


class ServiceUnavailableError(AppException):
    """下游服務（Redis / 外部 API）不可用（infra-monitoring.md §5）。"""

    status_code: int = 503
    error_code: str = "service_unavailable"
