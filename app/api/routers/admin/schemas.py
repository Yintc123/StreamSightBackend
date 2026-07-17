"""HTTP response schemas for admin endpoints."""

from pydantic import BaseModel, ConfigDict


class AdminResponse(BaseModel):
    """Public view of an Admin（不含 password_hash、不含型別判別欄 role）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    is_active: bool
