"""HTTP response schemas for auth endpoints.

繼承 `app.dtos.auth.TokenPayload`（service 契約），
在此加 API-specific 欄位（例如 expires_in / refresh_token）而不污染 domain contract。
目前尚無擴充、留擴充點供未來 API 演化。
"""

from app.dtos import TokenPayload


class TokenResponse(TokenPayload):
    """Response body for successful login / register."""
