from sqlalchemy import Select, select
from sqlalchemy.engine import Result

from app.models.admin import Admin

from .base import BaseRepository


class AdminRepository(BaseRepository[Admin]):
    """admins 表存取。`get`（PK lookup）繼承自 BaseRepository。"""

    model: type[Admin] = Admin

    async def get_by_email(self, email: str) -> Admin | None:
        """Fetch an admin by email（deterministic encryption → 明文比對命中密文）。"""
        stmt: Select[tuple[Admin]] = select(Admin).where(Admin.email == email)
        result: Result[tuple[Admin]] = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_principal_id(self, principal_id: int) -> Admin | None:
        """Fetch an admin by principal_id（refresh / 授權 dependency 依 role 分流後用）。"""
        stmt: Select[tuple[Admin]] = select(Admin).where(Admin.principal_id == principal_id)
        result: Result[tuple[Admin]] = await self.session.execute(stmt)
        return result.scalar_one_or_none()
