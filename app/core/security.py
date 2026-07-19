"""PII masking + admin 帳號欄位政策（單一真相）utilities。

Masking is for logging/display only — DB storage, email sending,
and business logic use raw values.
"""

import re

# admin 帳號欄位政策（單一真相）：DTO（AdminCreateRequest…）與 bootstrap `_validate_admin_fields` 共用。
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128
MAX_NAME_LEN = 100
# username 格式（正規化後）：小寫英數 . _ -，長度 3-100。見 admin-account-refinement §2.1。
USERNAME_RE: re.Pattern[str] = re.compile(r"^[a-z0-9._-]{3,100}$")


def validate_admin_password(password: str) -> None:
    """密碼政策：長度 8-128。不符 → ValueError（呼叫端轉對應例外/fail-fast）。"""
    if not (MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN):
        raise ValueError(f"password must be {MIN_PASSWORD_LEN}-{MAX_PASSWORD_LEN} chars")


def validate_admin_username(username: str) -> None:
    """username 格式（正規化後）。不符 → ValueError。"""
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("invalid admin username format")


def normalize_username(raw: str) -> str:
    """Admin username 正規化（單一事實來源）：strip + lower。

    由 DTO validator（AdminLoginRequest）、AdminService.create、seed 共用，
    確保 `Admin`/`admin`/` admin ` 都收斂為同一登入識別、唯一約束一致。
    見 docs/specs/admin-account-refinement.md §2.1。
    """
    return raw.strip().lower()


def mask_email(email: str) -> str:
    """
    Mask email address for safe logging.

    Preserves first 2 chars of the local part and the full domain.

    Examples:
        >>> mask_email("alice@example.com")
        'al***@example.com'
        >>> mask_email("a@b.com")
        'a***@b.com'
        >>> mask_email("ab@example.com")
        'a***@example.com'
        >>> mask_email("invalid")
        '***'
    """
    if not email or "@" not in email:
        return "***"

    local, domain = email.split("@", 1)

    if not local or not domain:
        return "***"

    if len(local) <= 2:
        return f"{local[0]}***@{domain}"

    return f"{local[:2]}***@{domain}"
