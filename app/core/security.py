"""PII masking utilities for logs and display.

Masking is for logging/display only — DB storage, email sending,
and business logic use raw values.
"""


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
