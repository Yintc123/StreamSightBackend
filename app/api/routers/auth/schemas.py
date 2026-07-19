"""HTTP response schemas for auth endpoints.

繼承 `app.dtos.dto_auth.TokenPayload`（service 契約），
在此加 API-specific 欄位（例如 expires_in）而不污染 domain contract。
"""

from pydantic import Field

from app.dtos import TokenPayload


class TokenResponse(TokenPayload):
    """Response body for successful login / register / refresh.

    繼承 access_token / token_type / refresh_token，另加 API 專屬的 expires_in
    （access token 剩餘有效秒數，OAuth2 慣例）。由 router 填入。
    """

    expires_in: int = Field(description="Access token lifetime in seconds")
