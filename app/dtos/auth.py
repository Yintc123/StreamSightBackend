"""
Auth domain DTOs - framework-agnostic (no FastAPI / SQLAlchemy imports).

用途：
    - Services 層接收的 input contract (register / login payload)
    - API 層 request body 也能復用
    - token response format
"""

from dataclasses import dataclass

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.enums import Role
from app.core.security import normalize_username

from .user import UserBase


@dataclass(frozen=True)
class CurrentPrincipal:
    """輕量值物件：由已驗簽 token 取 (principal_id, role)，**不查 DB**。

    供 logout-all 等「角色無關、只需當前身分 id/role」的端點（見 §5.6）。
    """

    id: int  # = sub（principal_id）
    role: Role


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


class AdminLoginRequest(BaseModel):
    """Payload for POST /admin/auth/login。

    必須獨立於 LoginRequest（其 email 型別為 EmailStr，會擋掉非 email 的 username）。
    於 DTO 邊界就正規化（單一入口，見 §2.1）；登入只正規化、不硬驗格式（§5.4）。
    """

    username: str = Field(min_length=1, max_length=100, description="Admin username")
    password: str = Field(description="Plain password")

    @field_validator("username")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return normalize_username(v)  # strip + lower


class RefreshRequest(BaseModel):
    """Payload for POST /auth/refresh and POST /auth/logout."""

    refresh_token: str = Field(description="Opaque refresh token")


class TokenPayload(BaseModel):
    """
    Token payload returned by AuthService (framework-agnostic).

    - access_token: JWT (short-lived)
    - token_type: OAuth2 convention，值為 bearer
    - refresh_token: opaque refresh token（rotation 時每次換新；login/register/refresh 都會帶）

    API 層若要加額外欄位（例如 expires_in），
    在 `app/api/routers/auth/schemas.py` 建 `TokenResponse(TokenPayload)` 擴充。
    """

    access_token: str = Field(description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type (RFC 6750)")
    refresh_token: str | None = Field(default=None, description="Opaque refresh token")
    access_token_expire_seconds: int | None = Field(
        default=None, description="Actual access token TTL in seconds; None = use global default"
    )
