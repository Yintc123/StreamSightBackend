from sqlalchemy import Delete, Select, select
from sqlalchemy import delete as sql_delete
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base


class BaseRepository[ModelT: Base]:
    """
    Generic async CRUD repository for SQLAlchemy models.

    subclass with a concrete model:
        class UserRepository(BaseRepository[User]):
            model = User

    Base 的 model 都有 int autoincrement PK；例外的複合 PK / UUID PK
    model 不會繼承 Base，本 repository 不服務它們。
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session

    async def get(self, id: int) -> ModelT | None:
        """Fetch by primary key. Returns None if not found."""
        return await self.session.get(self.model, id)

    async def list_all(self, offset: int = 0, limit: int = 100) -> list[ModelT]:
        """List rows ordered by primary key."""
        stmt: Select[tuple[ModelT]] = (
            select(self.model).order_by(self.model.id).offset(offset).limit(limit)
        )

        result: Result[tuple[ModelT]] = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, entity: ModelT) -> ModelT:
        """Add entity to session and flush to get generated fields (id, timestamps)."""
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        """Delete an entity."""
        await self.session.delete(entity)
        await self.session.flush()

    async def delete_by_id(self, id: int) -> int:
        """Bulk delete by primary key. Returns number of rows deleted."""
        stmt: Delete = sql_delete(self.model).where(self.model.id == id)
        # DELETE 用 CursorResult（有 rowcount），SELECT 用 Result（無 rowcount）
        result: CursorResult[tuple[int]] = await self.session.execute(stmt)  # pyright: ignore[reportAssignmentType]
        return result.rowcount or 0
