"""Records domain 例外——繼承既有通用基類以承接 status_code / error_code（records-model.md §0）。

base.py 維持純通用例外；全域 handler 零改動（只認 AppException，映射由基類屬性帶）。
"""

from .base import BusinessRuleError, NotFoundError


class RecordNotFoundError(NotFoundError):
    """get/update/delete 遇不存在或已軟刪除的 record（→ 404）。"""

    error_code: str = "record_not_found"


class RecordValidationError(BusinessRuleError):
    """欄位語意不合法（category 名不存在/寫入用 inactive、sort 欄非法、size/page 非法、匯入超限）（→ 422）。"""

    error_code: str = "record_validation_error"
