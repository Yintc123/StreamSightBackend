"""AdminService — CMS 管理者的建立（seed 用）／查詢／刪除。

見 docs/specs/jwt-role-and-admin.md §5.5、決策 D5/D7。Admin 不公開註冊，
初始 admin 由 seed script 走 `create` 佈建。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password
from app.core.enums import Role
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import mask_email
from app.models.admin import Admin
from app.repositories.admin import AdminRepository
from app.repositories.principal import PrincipalRepository

logger: logging.Logger = logging.getLogger(__name__)


class AdminService:
    """Business logic for Admin domain.

    Transaction Boundary: `create` / `delete` 各持有唯一一次 commit（Unit-of-Work）。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session
        self.repo: AdminRepository = AdminRepository(session)
        self.principal_repo: PrincipalRepository = PrincipalRepository(session)

    async def get(self, admin_id: int) -> Admin:
        """Fetch an admin by ID. Raises NotFoundError if missing."""
        admin: Admin | None = await self.repo.get(admin_id)
        if admin is None:
            raise NotFoundError(f"Admin {admin_id} not found")
        return admin

    async def get_by_email(self, email: str) -> Admin | None:
        return await self.repo.get_by_email(email)

    async def get_by_principal_id(self, principal_id: int) -> Admin | None:
        return await self.repo.get_by_principal_id(principal_id)

    async def create(self, email: str, name: str, password: str) -> Admin:
        """建 principal(role=1) + admin（argon2 hash 密碼），同一交易原子落地。

        Raises ConflictError if the admin email is already taken.
        """
        if await self.repo.get_by_email(email) is not None:
            raise ConflictError(
                f"Admin {mask_email(email)} already registered", details={"field": "email"}
            )

        password_hash: str = await hash_password(password)
        try:
            principal = await self.principal_repo.create(Role.ADMIN)  # flush
            admin: Admin = Admin(
                email=email,
                name=name,
                password_hash=password_hash,
                principal_id=principal.id,
            )
            admin = await self.repo.add(admin)  # flush
            await self.session.commit()  # 唯一一次 commit
            logger.info("Created admin id=%s email=%s", admin.id, mask_email(email))
            return admin
        except Exception:
            await self.session.rollback()
            raise

    async def delete(self, admin_id: int) -> None:
        """刪 admin：解析 principal_id → 刪 principals 該列，CASCADE 連帶清 admin + refresh_tokens。"""
        admin: Admin = await self.get(admin_id)
        principal = await self.principal_repo.get(admin.principal_id)
        try:
            if principal is not None:
                await self.principal_repo.delete(principal)  # CASCADE → admin + refresh_tokens
            await self.session.commit()
            # admin 經 DB CASCADE 刪除（非 ORM）→ 逐出 session 避免 identity map stale 快取
            self.session.expunge(admin)
            logger.info("Deleted admin id=%s email=%s", admin_id, mask_email(admin.email))
        except Exception:
            await self.session.rollback()
            raise
