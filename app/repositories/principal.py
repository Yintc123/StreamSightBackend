from app.core.enums import Role
from app.models.principal import Principal

from .base import BaseRepository


class PrincipalRepository(BaseRepository[Principal]):
    """principals 父表存取。`get`（PK lookup，refresh 取 role 用）繼承自 BaseRepository。"""

    model: type[Principal] = Principal

    async def create(self, role: Role) -> Principal:
        """建一筆 principal（供 register / admin 建立）。呼叫端負責 commit。"""
        principal: Principal = Principal(role=int(role))
        return await self.add(principal)
