from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.engine import Result
from sqlalchemy.orm import aliased

from app.core.enums import AdminStatusFilter
from app.models.admin import Admin

from .base import BaseRepository


@dataclass(frozen=True)
class AdminListRow:
    """list_admins 的一列：admin 本體 + 解析出的稽核者 username（供 API 直接顯示）。

    裸 archived_by / deleted_by（principal_id）仍保留在 admin 上作穩定參照；
    這裡額外帶「誰封存/刪除」的 username，免前端二次查詢（model §4 L1）。
    """

    admin: Admin
    archived_by_username: str | None
    deleted_by_username: str | None


def _status_predicate(status: AdminStatusFilter) -> ColumnElement[bool] | None:
    """把狀態篩選轉為時間戳謂詞（is_active 為計算屬性、不可進 SQL，model §2.7）。"""
    if status is AdminStatusFilter.ACTIVE:
        return (Admin.archived_at.is_(None)) & (Admin.deleted_at.is_(None))
    if status is AdminStatusFilter.ARCHIVED:
        return (Admin.archived_at.is_not(None)) & (Admin.deleted_at.is_(None))
    if status is AdminStatusFilter.DELETED:
        return Admin.deleted_at.is_not(None)
    return None  # ALL：不篩


class AdminRepository(BaseRepository[Admin]):
    """admins 表存取。`get`（PK lookup）繼承自 BaseRepository。"""

    model: type[Admin] = Admin

    async def get_by_username(self, username: str) -> Admin | None:
        """Fetch an admin by username（非加密明文唯一索引，直接比對）。

        回原列（含已封存／軟刪除者），可用性由呼叫端讀 is_active 計算屬性判定（見 §5.2）。
        """
        stmt: Select[tuple[Admin]] = select(Admin).where(Admin.username == username)
        result: Result[tuple[Admin]] = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_principal_id(self, principal_id: int) -> Admin | None:
        """Fetch an admin by principal_id（refresh / 授權 dependency 依 role 分流後用）。"""
        stmt: Select[tuple[Admin]] = select(Admin).where(Admin.principal_id == principal_id)
        result: Result[tuple[Admin]] = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_admins(
        self, *, status: AdminStatusFilter, limit: int, offset: int
    ) -> Sequence[AdminListRow]:
        """依狀態謂詞列出 admins，`ORDER BY id` 穩定分頁。

        以兩次 LEFT JOIN admins（archived_by / deleted_by → principal_id）解析操作者
        username，供 API 的 AdminSummary 直接顯示「誰封存/刪除」（model §4 L1）。
        """
        archiver = aliased(Admin)
        deleter = aliased(Admin)
        stmt = (
            select(Admin, archiver.username, deleter.username)
            .outerjoin(archiver, archiver.principal_id == Admin.archived_by)
            .outerjoin(deleter, deleter.principal_id == Admin.deleted_by)
            .order_by(Admin.id)
            .offset(offset)
            .limit(limit)
        )
        predicate = _status_predicate(status)
        if predicate is not None:
            stmt = stmt.where(predicate)

        result = await self.session.execute(stmt)
        return [
            AdminListRow(
                admin=row[0],
                archived_by_username=row[1],
                deleted_by_username=row[2],
            )
            for row in result.all()
        ]

    async def get_list_row(self, admin_id: int) -> AdminListRow | None:
        """單列版 list_admins：帶稽核者 username 的一列（供明細／生命週期回身共用）。"""
        archiver = aliased(Admin)
        deleter = aliased(Admin)
        stmt = (
            select(Admin, archiver.username, deleter.username)
            .outerjoin(archiver, archiver.principal_id == Admin.archived_by)
            .outerjoin(deleter, deleter.principal_id == Admin.deleted_by)
            .where(Admin.id == admin_id)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return AdminListRow(admin=row[0], archived_by_username=row[1], deleted_by_username=row[2])

    async def count_admins(self, *, status: AdminStatusFilter) -> int:
        """同狀態謂詞計數（供分頁 total）。"""
        stmt = select(func.count()).select_from(Admin)
        predicate = _status_predicate(status)
        if predicate is not None:
            stmt = stmt.where(predicate)
        result = await self.session.execute(stmt)
        return result.scalar_one()
