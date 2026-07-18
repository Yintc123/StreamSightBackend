"""AdminService — CMS 管理者的建立（seed 用）／查詢／封存／軟刪除／復原。

見 docs/specs/admin-account-refinement.md §5.3。Admin 不公開註冊，
初始 admin 由 seed script 走 `create` 佈建。狀態機（archive/unarchive/delete/restore）
只到 service 層，HTTP 端點留待 admin 管理 API 規格。
"""

import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password, verify_password
from app.core.enums import ADMIN_ROLE_RANK, AdminRole, AdminStatusFilter, Role
from app.core.exceptions import (
    BadRequestError,
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.security import normalize_username
from app.models.admin import Admin
from app.repositories.admin import AdminListRow, AdminRepository
from app.repositories.principal import PrincipalRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.services.initial_admin import INITIAL_ADMIN_PRINCIPAL_ID, is_initial_admin_username
from app.services.ws.publisher import Publisher

logger: logging.Logger = logging.getLogger(__name__)

# 建立路徑強制的 username 格式（正規化後）：小寫英數與 ._- ，長度 3–100。
# 登入路徑只正規化、不硬驗格式（格式不符 → 統一 401，不 422，見 §2.1）。
_USERNAME_RE: re.Pattern[str] = re.compile(r"^[a-z0-9._-]{3,100}$")


class AdminService:
    """Business logic for Admin domain.

    Transaction Boundary: create / archive / unarchive / delete / restore 各持有唯一一次 commit。
    """

    def __init__(self, session: AsyncSession, publisher: Publisher | None = None) -> None:
        self.session: AsyncSession = session
        self.repo: AdminRepository = AdminRepository(session)
        self.principal_repo: PrincipalRepository = PrincipalRepository(session)
        self.refresh_repo: RefreshTokenRepository = RefreshTokenRepository(session)
        # 撤權時即時斷 WS（best-effort kick；可靠性由 WS 定期複查兜底，websocket §2.5）。
        self.publisher: Publisher | None = publisher

    async def _kick_principal(self, principal_id: int) -> None:
        """best-effort：發佈 Redis kick 斷該 principal 全部 WS。失敗不影響已提交的撤權動作。"""
        if self.publisher is None:
            return
        try:
            await self.publisher.disconnect_principal(principal_id)
        except Exception:
            logger.warning("WS kick publish failed principal=%s", principal_id, exc_info=True)

    async def list_admins(
        self,
        *,
        status: AdminStatusFilter = AdminStatusFilter.ACTIVE,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[AdminListRow], int]:
        """列表（委派 repository）：回 (rows, total)。純讀取、無 commit。§3.8。

        rows 為 AdminListRow（admin + 解析出的稽核者 username，供 API 直接顯示）。
        """
        rows = await self.repo.list_admins(status=status, limit=limit, offset=offset)
        total = await self.repo.count_admins(status=status)
        return rows, total

    async def get_row(self, admin_id: int, *, include_deleted: bool = False) -> AdminListRow:
        """明細／生命週期回身用：帶稽核者 username 的一列。軟刪除規則同 `get`。

        Raises:
            NotFoundError: admin 不存在，或未帶 include_deleted 時已軟刪除。
        """
        row = await self.repo.get_list_row(admin_id)
        if row is None or (not include_deleted and row.admin.deleted_at is not None):
            raise NotFoundError(f"Admin {admin_id} not found")
        return row

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
        is_protected: bool = False,
    ) -> Admin:
        """建 principal(role=1) + admin（argon2 hash 密碼），同一交易原子落地。

        username 正規化（小寫）＋格式驗證後儲存；admin_role 預設 VIEWER（最低權限 fail-safe），
        seed 傳 SUPER_ADMIN。is_protected 預設 False（管理 API 一律 False）；seed 建 root 傳 True
        （§3.1/§3.7）——受保護者必為 super_admin，否則 CHECK ck_admins_protected_is_super 擋下。

        Raises:
            BadRequestError: username 格式不符 _USERNAME_RE
            ConflictError: username 已被使用（含大小寫變體）
            IntegrityError: is_protected=True 但 admin_role != SUPER_ADMIN（CHECK）
        """
        u: str = normalize_username(username)
        if not _USERNAME_RE.fullmatch(u):
            raise BadRequestError("Invalid admin username format", details={"field": "username"})
        # 初始 super admin username 為保留字（避免 DB admin 遮蔽／混淆特例帳號）
        if is_initial_admin_username(u):
            raise ConflictError(f"Admin {u} already registered", details={"field": "username"})
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
                is_protected=is_protected,
                principal_id=principal.id,
            )
            admin = await self.repo.add(admin)  # flush
            await self.session.commit()  # 唯一一次 commit
            logger.info("Created admin id=%s username=%s", admin.id, u)
            return admin
        except Exception:
            await self.session.rollback()
            raise

    async def update(self, admin_id: int, *, name: str, actor_principal_id: int) -> Admin:
        """更新顯示名稱（不接受 username／不改權限／不撤 token）。§3.2。

        軟刪除者 → NotFoundError。log actor → target。

        Raises:
            NotFoundError: admin 不存在或已軟刪除。
        """
        admin: Admin = await self.get(admin_id)
        admin.name = name
        await self.session.commit()
        logger.info("Updated admin id=%s name by actor=%s", admin_id, actor_principal_id)
        return admin

    async def change_password(
        self, admin_id: int, *, current_password: str, new_password: str
    ) -> None:
        """自助改自己密碼（驗舊）：換 hash 後撤該 principal 全部 refresh token（強制重登）。§3.3。

        此路徑帳號必存在（呼叫者本人、已過 get_current_admin），故直接 verify、不需 dummy。
        新密碼不得等於舊——以 verify_password(new, 舊 hash) 判定（argon2 隨機 salt 無法直接比 hash）。

        Raises:
            NotFoundError: admin 不存在或已軟刪除。
            UnauthorizedError: 舊密碼不符（統一訊息）。
            BadRequestError: 新密碼等於舊密碼。
            ForbiddenError: 初始 super admin（憑證由 SSM 管理、不可經 API 改）。
        """
        # 初始 super admin（哨兵 id）憑證存 SSM，不可經 API 改密碼。
        if admin_id == INITIAL_ADMIN_PRINCIPAL_ID:
            raise ForbiddenError("Initial admin password is managed out-of-band (SSM)")
        admin: Admin = await self.get(admin_id)
        if not await verify_password(current_password, admin.password_hash):
            raise UnauthorizedError("Invalid credentials")
        if await verify_password(new_password, admin.password_hash):
            raise BadRequestError("New password must differ from the current password")
        admin.password_hash = await hash_password(new_password)
        now: datetime = datetime.now(UTC)
        await self.refresh_repo.revoke_all_for_principal(admin.principal_id, now)
        await self.session.commit()
        await self._kick_principal(admin.principal_id)
        logger.info("Admin id=%s changed own password; tokens revoked", admin_id)

    async def set_admin_role(
        self, admin_id: int, *, admin_role: AdminRole, actor_principal_id: int
    ) -> Admin:
        """升降權（改 admin_role 權限等級，非型別判別子 role）。授權即時（讀 child）。

        執行順序（H2：idempotent 先於守衛）：
          1. get（軟刪除 → NotFoundError）。
          2. idempotent early-return：等級未變 → 直接回（在守衛前，避免「對受保護 root 設回
             super_admin」被守衛誤擋）。
          3. 受保護守衛（單列）：受保護者被降級（→非 super_admin）→ BusinessRuleError(422)。
          4. 自我提權守衛（單列）：actor==target 且新等級 rank 更高 → BusinessRuleError(422)。
          5. 設值 → commit。不撤 token（授權讀 child 現值 → 降權即時；grade 由 refresh 刷新）。

        Raises:
            NotFoundError: admin 不存在或已軟刪除。
            BusinessRuleError: 降級受保護 root／自我提權（皆 422）。
        """
        admin: Admin = await self.get(admin_id)
        # H2：idempotent 先於守衛
        if admin.admin_role == admin_role.value:
            return admin
        # 受保護守衛（單列）：受保護者不可降級（→非 super_admin）
        if admin.is_protected and admin_role is not AdminRole.SUPER_ADMIN:
            raise BusinessRuleError("cannot demote the protected root admin")
        # 自我提權守衛（單列）：本人不可把自己升到更高等級
        if (
            actor_principal_id == admin.principal_id
            and ADMIN_ROLE_RANK[admin_role] > ADMIN_ROLE_RANK[AdminRole(admin.admin_role)]
        ):
            raise BusinessRuleError("cannot elevate your own role")
        admin.admin_role = admin_role.value
        await self.session.commit()
        logger.info(
            "Set admin id=%s admin_role=%s by actor=%s",
            admin_id,
            admin_role.value,
            actor_principal_id,
        )
        return admin

    def _guard_transition(
        self, admin: Admin, actor_principal_id: int | None, *, action: str
    ) -> None:
        """封存／軟刪除的單列守衛（authoritative，繞過 api 亦安全）。§3.5。

        M3：受保護／super_admin 守衛**恆適用**（含 actor=None 的 seed/script）；禁對自己僅在
        有 actor 時適用。皆只讀 target 自己那一列——無聚合、無鎖、無 write skew。
        """
        if admin.is_protected:
            raise BusinessRuleError(f"cannot {action} the protected root admin")
        if admin.admin_role == AdminRole.SUPER_ADMIN.value:
            raise BusinessRuleError(f"demote before {action[:-1]}ing a super admin")
        if actor_principal_id is not None and actor_principal_id == admin.principal_id:
            raise BusinessRuleError(f"cannot {action} yourself")

    async def archive(self, admin_id: int, *, actor_principal_id: int | None = None) -> Admin:
        """封存（active→archived）：設 archived_at/by、撤該 principal 的 refresh token。

        已封存則 idempotent 直接回（在守衛前）。成對寫入 archived_at 與 archived_by（見 §2.2）。
        受保護／super_admin-須先降級／禁對自己守衛見 §3.5。
        """
        admin: Admin = await self.get(admin_id)
        if admin.archived_at is not None:
            return admin  # idempotent（在守衛前）
        self._guard_transition(admin, actor_principal_id, action="archive")
        now: datetime = datetime.now(UTC)
        admin.archived_at = now
        admin.archived_by = actor_principal_id
        await self.refresh_repo.revoke_all_for_principal(admin.principal_id, now)
        await self.session.commit()
        await self._kick_principal(admin.principal_id)
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
        受保護／super_admin-須先降級／禁對自己守衛見 §3.5。
        """
        admin: Admin = await self.get(admin_id)
        self._guard_transition(admin, actor_principal_id, action="delete")
        now: datetime = datetime.now(UTC)
        admin.deleted_at = now
        admin.deleted_by = actor_principal_id
        await self.refresh_repo.revoke_all_for_principal(admin.principal_id, now)
        await self.session.commit()
        await self._kick_principal(admin.principal_id)
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
