"""
Auth domain DTOs - framework-agnostic (no FastAPI / SQLAlchemy imports).

用途：
    - Services 層接收的 input contract (register / login payload)
    - API 層 request body 也能復用
    - token response format
"""

from pydantic import BaseModel, EmailStr, Field

from .user import UserBase


class RegisterRequest(UserBase):
    """Payload for POST /auth/register.

    繼承 UserBase (email + name) — 未來 UserBase 加共通欄位（如 phone）自動同步。
    """

    password: str = Field(
        min_length=8,
        max_length=128,
        description="Plain password (>=8 chars; will be hashed with argon2 before storage)",
    )


class LoginRequest(BaseModel):
    """Payload for POST /auth/login."""

    email: EmailStr = Field(description="User email address")
    password: str = Field(description="Plain password")


class TokenPayload(BaseModel):
    """
    Token payload returned by AuthService (framework-agnostic).

    - access_token: JWT (short-lived)
    - token_type: OAuth2 convention，值為 bearer

    API 層若要加額外欄位（例如 expires_in / refresh_token），
    在 `app/api/routers/auth/schemas.py` 建 `TokenResponse(TokenPayload)` 擴充。
    """

    access_token: str = Field(description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type (RFC 6750)")
