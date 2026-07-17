"""HTTP response schemas for admin endpoints."""

from pydantic import BaseModel, ConfigDict

from app.core.enums import AdminRole


class AdminResponse(BaseModel):
    """Public view of an Admin（不含 password_hash、不含型別判別欄 role）。

    含 admin_role：/admin/me 是 admin 讀取自身，其權限等級對前端渲染 CMS 選單／按鈕有意義，
    且為 rbac 等級的權威來源。不含狀態時間戳：登入者恆為 active（見 §5.1）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    name: str
    admin_role: AdminRole
