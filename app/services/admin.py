"""AdminService — CMS 管理者的建立（seed 用）／查詢／封存／軟刪除／復原。

見 docs/specs/admin-account-refinement.md §5.3。Admin 不公開註冊，
初始 admin 由 seed script 走 `create` 佈建。狀態機（archive/unarchive/delete/restore）
只到 service 層，HTTP 端點留待 admin 管理 API 規格。
"""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password
from app.core.enums import AdminRole, Role
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.security import normalize_username
from app.models.admin import Admin
from app.repositories.admin import AdminRepository
from app.repositories.principal import PrincipalRepository
from app.repositories.refresh_token import RefreshTokenRepository

logger: logging.Logger = logging.getLogger(__name__)

# 建立路徑強制的 username 格式（正規化後）：小寫英數與 ._- ，長度 3–100。
# 登入路徑只正規化、不硬驗格式（格式不符 → 統一 401，不 422，見 §2.1）。
_USERNAME_RE: re.Pattern[str] = re.compile(r"^[a-z0-9._-]{3,100}$")


class AdminService:
    """Business logic for Admin domain.

    Transaction Boundary: create / archive / unarchive / delete / restore 各持有唯一一次 commit。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session
        self.repo: AdminRepository = AdminRepository(session)
        self.principal_repo: PrincipalRepository = PrincipalRepository(session)
        self.refresh_repo: RefreshTokenRepository = RefreshTokenRepository(session)

    async def get(self, admin_id: int, *, include_deleted: bool = False) -> Admin:
        """Fetch an admin by ID. Raises NotFoundError if missing.

        預設把軟刪除（deleted_at 有值）視為不存在；管理／復原情境傳 include_deleted=True。
        """
        admin: Admin | None = await self.repo.get(admin_id)
        if admin is None or (not include_deleted and admin.deleted_at is not None):
            raise NotFoundError(f"Admin {admin_id} not found")
        return admin

    async def get_by_username(self, username: str) -> Admin | None:
        return await self.repo.get_by_username(username)

    async def get_by_principal_id(self, principal_id: int) -> Admin | None:
        return await self.repo.get_by_principal_id(principal_id)

    async def create(
        self,
        username: str,
        name: str,
        password: str,
        admin_role: AdminRole = AdminRole.VIEWER,
    ) -> Admin:
        """建 principal(role=1) + admin（argon2 hash 密碼），同一交易原子落地。

        username 正規化（小寫）＋格式驗證後儲存；admin_role 預設 VIEWER（最低權限 fail-safe），
        seed 傳 SUPER_ADMIN。

        Raises:
            BadRequestError: username 格式不符 _USERNAME_RE
            ConflictError: username 已被使用（含大小寫變體）
        """
        u: str = normalize_username(username)
        if not _USERNAME_RE.fullmatch(u):
            raise BadRequestError("Invalid admin username format", details={"field": "username"})
        if await self.repo.get_by_username(u) is not None:
            raise ConflictError(f"Admin {u} already registered", details={"field": "username"})

        password_hash: str = await hash_password(password)
        try:
            principal = await self.principal_repo.create(Role.ADMIN)  # flush
            admin: Admin = Admin(
                username=u,
                name=name,
                password_hash=password_hash,
                admin_role=admin_role.value,
                principal_id=principal.id,
            )
            admin = await self.repo.add(admin)  # flush
            await self.session.commit()  # 唯一一次 commit
            logger.info("Created admin id=%s username=%s", admin.id, u)
            return admin
        except Exception:
            await self.session.rollback()
            raise

    async def archive(self, admin_id: int, *, actor_principal_id: int | None = None) -> Admin:
        """封存（active→archived）：設 archived_at/by、撤該 principal 的 refresh token。

        已封存則 idempotent 直接回。成對寫入 archived_at 與 archived_by（見 §2.2）。
        """
        admin: Admin = await self.get(admin_id)
        if admin.archived_at is not None:
            return admin
        now: datetime = datetime.now(UTC)
        admin.archived_at = now
        admin.archived_by = actor_principal_id
        await self.refresh_repo.revoke_all_for_principal(admin.principal_id, now)
        await self.session.commit()
        logger.info("Archived admin id=%s", admin_id)
        return admin

    async def unarchive(self, admin_id: int) -> Admin:
        """解除封存（archived→active）：成對清 archived_at 與 archived_by。不自動復發 token。"""
        admin: Admin = await self.get(admin_id)
        admin.archived_at = None
        admin.archived_by = None
        await self.session.commit()
        logger.info("Unarchived admin id=%s", admin_id)
        return admin

    async def delete(self, admin_id: int, *, actor_principal_id: int | None = None) -> None:
        """軟刪除（active/archived→deleted）：設 deleted_at/by、撤 refresh token。

        不刪 principals、不觸發 CASCADE（見 §2.5）。對已刪者再 delete → NotFoundError。
        """
        admin: Admin = await self.get(admin_id)
        now: datetime = datetime.now(UTC)
        admin.deleted_at = now
        admin.deleted_by = actor_principal_id
        await self.refresh_repo.revoke_all_for_principal(admin.principal_id, now)
        await self.session.commit()
        logger.info("Soft-deleted admin id=%s", admin_id)

    async def restore(self, admin_id: int) -> Admin:
        """復原軟刪除（deleted→active）：成對清 deleted_at 與 deleted_by。不自動復發 token。

        對未刪除者 idempotent 直接回。走 include_deleted=True 取回該列。
        """
        admin: Admin = await self.get(admin_id, include_deleted=True)
        if admin.deleted_at is None:
            return admin
        admin.deleted_at = None
        admin.deleted_by = None
        await self.session.commit()
        logger.info("Restored admin id=%s", admin_id)
        return admin
