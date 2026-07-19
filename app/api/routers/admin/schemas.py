"""HTTP request / response schemas for admin endpoints。

AdminResponse（精簡，操作單一 active admin 的回身）vs AdminSummary（含狀態／is_protected，
列表與明細）。見 admin-management-api.md §4。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import AdminRole
from app.core.security import normalize_username

if TYPE_CHECKING:
    from app.repositories.repo_admin import AdminListRow


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


# ── requests ──


class AdminCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    admin_role: AdminRole = AdminRole.VIEWER  # fail-safe；is_protected 不對外（恆 False）

    @field_validator("username")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_username(v)


class AdminUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)  # 只改 name（username 不可變、role 走 /role）


class AdminRoleUpdateRequest(BaseModel):
    admin_role: AdminRole


class ChangeOwnPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


# ── responses ──


class AdminSummary(BaseModel):
    """管理列表／明細用：含狀態、稽核與 is_protected（§4.2）。"""

    id: int
    username: str
    name: str
    admin_role: AdminRole
    is_protected: bool  # 前端據此標示「root、不可移除」並禁用相關按鈕
    is_active: bool  # 計算屬性
    archived_at: datetime | None
    archived_by: int | None  # 操作者 principal_id（穩定參照）
    archived_by_username: str | None  # 操作者 username（repo 自 join 解析，L1）
    deleted_at: datetime | None
    deleted_by: int | None
    deleted_by_username: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: AdminListRow) -> AdminSummary:
        a = row.admin
        return cls(
            id=a.id,
            username=a.username,
            name=a.name,
            admin_role=AdminRole(a.admin_role),
            is_protected=a.is_protected,
            is_active=a.is_active,
            archived_at=a.archived_at,
            archived_by=a.archived_by,
            archived_by_username=row.archived_by_username,
            deleted_at=a.deleted_at,
            deleted_by=a.deleted_by,
            deleted_by_username=row.deleted_by_username,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )


class AdminListResponse(BaseModel):
    items: list[AdminSummary]
    total: int
    limit: int
    offset: int
