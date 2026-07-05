# AppException 屬於業務層；HTTPException 屬於 framework 層
class AppException(Exception):
    """
    全部業務層的 exceptions 都從這個 class 繼承，
    子類別設置 status_code 和 error_code 作為子類別的屬性
    """
    status_code: int = 500
    error_code: str = "internal_error"

    # * 強制之後的參數帶上 keyword
    # raise NotFoundError("user", {"id": 1}) -> TypeError，強制要求 keyword
    # raise NotFoundError("user", details={"id": 1}) -> 正確
    def __init__(self, message: str = "", *, details: dict | None = None) -> None:
        # 將 message 傳給 Exception 的 args，讓 traceback 和 log 可以追蹤
        super().__init__(message)
        self.message: str = message
        self.details: dict = details or {}

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
    