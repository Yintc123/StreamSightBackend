"""HTTP response schemas for user endpoints.

Input DTOs (UserCreate / UserUpdate) 定義在 `app.dtos.user` — framework-agnostic。
本檔案只放 HTTP-specific 的 output shape（server-generated 欄位、from_attributes 設定）。
"""

from datetime import datetime

from pydantic import ConfigDict, Field

from app.dtos import UserBase


class UserResponse(UserBase):
    """Response body for user endpoints."""
    # 讓 Pydantic 從 SQLAlchemy 物件建立
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="User ID")
    is_active: bool = Field(description="Whether the account is active")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
