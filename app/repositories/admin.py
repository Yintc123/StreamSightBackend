from sqlalchemy import Select, select
from sqlalchemy.engine import Result

from app.models.admin import Admin

from .base import BaseRepository


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
