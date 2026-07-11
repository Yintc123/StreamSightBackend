"""User domain DTOs — framework-agnostic (no FastAPI / SQLAlchemy imports).

用途：
    - Services 層接收的 input contract（create / update payload）
    - API 層 request body 也 reuse 這些 DTO 做驗證

分離理由：
    - 讓 services 層不用 import `app.api.routers.*`
    - CLI / worker / batch job 都能用同一組 DTO
"""

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    """Fields shared across user input DTOs."""
    email: EmailStr = Field(description="User email address")
    name: str = Field(min_length=1, max_length=100, description="Display name")


class UserCreate(UserBase):
    """Payload for creating a user."""


class UserUpdate(BaseModel):
    """Payload for partial user update. All fields optional."""
    email: EmailStr | None = Field(default=None, description="New email")
    name: str | None = Field(default=None, min_length=1, max_length=100, description="New name")
    is_active: bool | None = Field(default=None, description="Enable/disable account")
