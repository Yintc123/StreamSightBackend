"""PII masking utilities for logs and display.

Masking is for logging/display only — DB storage, email sending,
and business logic use raw values.
"""


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
