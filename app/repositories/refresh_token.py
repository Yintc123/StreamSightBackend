from datetime import datetime

from sqlalchemy import Delete, Select, Update, select
from sqlalchemy import delete as sql_delete
from sqlalchemy import update as sql_update
from sqlalchemy.engine import CursorResult, Result

from app.models.refresh_token import RefreshToken

from .base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model: type[RefreshToken] = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Fetch a refresh token row by its stored hash. Returns None if not found."""
        stmt: Select[tuple[RefreshToken]] = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash
        )
        result: Result[tuple[RefreshToken]] = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def consume(
        self, token_id: int, revoked_at: datetime, replaced_by_id: int | None = None
    ) -> bool:
        """Atomically revoke an *active* token (rotation 消費，見 spec 2.5）。

        `UPDATE ... WHERE id=:id AND revoked_at IS NULL`，回傳是否搶到（rowcount == 1）。
        並發下只有一個請求會成功；其餘拿到 rowcount 0。
        """
        stmt: Update = (
            sql_update(RefreshToken)
            .where(RefreshToken.id == token_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at, replaced_by_id=replaced_by_id)
            .execution_options(synchronize_session=False)
        )
        result: CursorResult[tuple[int]] = await self.session.execute(stmt)  # pyright: ignore[reportAssignmentType]
        return (result.rowcount or 0) == 1

    async def revoke_family(self, family_id: str, revoked_at: datetime) -> int:
        """Revoke all still-active tokens in a family (reuse detection 連坐)。回傳撤銷筆數。"""
        stmt: Update = (
            sql_update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
            .execution_options(synchronize_session=False)
        )
        result: CursorResult[tuple[int]] = await self.session.execute(stmt)  # pyright: ignore[reportAssignmentType]
        return result.rowcount or 0

    async def revoke_all_for_principal(self, principal_id: int, revoked_at: datetime) -> int:
        """Revoke all still-active tokens for a principal (logout-all，角色無關)。回傳撤銷筆數。"""
        stmt: Update = (
            sql_update(RefreshToken)
            .where(RefreshToken.principal_id == principal_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
            .execution_options(synchronize_session=False)
        )
        result: CursorResult[tuple[int]] = await self.session.execute(stmt)  # pyright: ignore[reportAssignmentType]
        return result.rowcount or 0

    async def delete_expired(self, before: datetime) -> int:
        """Delete rows whose expires_at <= before (cleanup)。回傳刪除筆數。"""
        stmt: Delete = (
            sql_delete(RefreshToken)
            .where(RefreshToken.expires_at <= before)
            .execution_options(synchronize_session=False)
        )
        result: CursorResult[tuple[int]] = await self.session.execute(stmt)  # pyright: ignore[reportAssignmentType]
        return result.rowcount or 0
