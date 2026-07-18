import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role, UserTier
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import mask_email
from app.dtos import UserCreate, UserUpdate
from app.models.user import User
from app.repositories.principal import PrincipalRepository
from app.repositories.user import UserRepository

logger: logging.Logger = logging.getLogger(__name__)


class UserService:
    """
    Business logic for User domain.

    Transaction Boundary: each public method commits (or rolls back on error).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session
        self.repo: UserRepository = UserRepository(session)
        self.principal_repo: PrincipalRepository = PrincipalRepository(session)

    async def get(self, user_id: int) -> User:
        """Fetch a user by ID. Raises NotFoundError if missing."""
        user: User | None = await self.repo.get(user_id)
        if user is None:
            raise NotFoundError(f"User {user_id} not found")
        return user

    async def list_all(self, offset: int = 0, limit: int = 100) -> list[User]:
        """List users ordered by ID."""
        return await self.repo.list_all(offset=offset, limit=limit)

    async def build(self, payload: UserCreate) -> User:
        """建 principal(role=0) + user，只 flush、**不 commit**（Unit-of-Work，見 D10）。

        供 use-case 方法（如 register）把多實體收斂到唯一一次 commit 原子落地。
        Raises ConflictError if email is taken.
        """
        if await self.repo.email_exists(payload.email):
            raise ConflictError(
                f"Email {mask_email(payload.email or '')} already registered",
                details={"field": "email"},
            )
        # 交易內兩步：先建 principal(role=0) 取得 id → 再建 user（帶 principal_id）
        principal = await self.principal_repo.create(Role.USER)
        user: User = User(email=payload.email, name=payload.name, principal_id=principal.id)
        return await self.repo.add(user)

    async def create(self, payload: UserCreate) -> User:
        """Create a new user (committing wrapper around `build`).

        認證 credential (password hash、OAuth sub) 存在 Identity 表、由 AuthService
        統一管理。此處只負責 User 這個實體。
        """
        try:
            user: User = await self.build(payload)
            await self.session.commit()
            logger.info("Created user id=%s email=%s", user.id, mask_email(user.email or ""))
            return user
        except Exception:
            await self.session.rollback()
            # unhandled_exception_handler 回 500
            raise

    async def update(self, user_id: int, payload: UserUpdate) -> User:
        """Partially update a user. Only non-None fields are applied."""
        user: User = await self.get(user_id)

        updates: dict[str, Any] = payload.model_dump(exclude_unset=True)

        # 若改 email，先檢查唯一性
        # 保留兩層：內層 await 是有副作用的 DB query，比外層 pure comparison 昂貴，
        # 分開比合併成一個大 if 表達更清楚 short-circuit 意圖
        if "email" in updates and updates["email"] != user.email:  # noqa: SIM102
            if await self.repo.email_exists(updates["email"]):
                raise ConflictError(
                    f"Email {mask_email(updates['email'])} already registered",
                    details={"field": "email"},
                )

        for key, value in updates.items():
            setattr(user, key, value)

        try:
            await self.session.flush()
            await self.session.commit()
            await self.session.refresh(user)
            logger.info("Updated user id=%s fields=%s", user.id, list(updates.keys()))
            return user
        except Exception:
            await self.session.rollback()
            raise

    async def set_tier(self, user_id: int, tier: UserTier) -> User:
        """升降級一般 User（寫 user_tier 現值）。授權即時（讀 child），見 rbac §5.1。"""
        user: User = await self.get(user_id)
        user.user_tier = tier.value
        try:
            await self.session.commit()
            logger.info("Set user id=%s tier=%s", user_id, tier.value)
            return user
        except Exception:
            await self.session.rollback()
            raise

    async def delete(self, user_id: int) -> None:
        """Delete a user by ID（以 principal 為單位刪除）。Raises NotFoundError if missing.

        解析 user.principal_id → 刪 principals 該列，`ON DELETE CASCADE` 連帶清掉
        user + identities + refresh_tokens，不留孤兒 principal（見 §2.6 / §5.4）。
        """
        user: User = await self.get(user_id)
        principal = await self.principal_repo.get(user.principal_id)
        try:
            if principal is not None:
                await self.principal_repo.delete(principal)  # CASCADE → user + identities + tokens
            await self.session.commit()
            # user 是被 DB 層 CASCADE 刪除的（非經 ORM），identity map 仍留著舊物件 →
            # 逐出 session，讓後續 get 命中 DB（回 None）而非 stale 快取。
            self.session.expunge(user)
            logger.info("Deleted user id=%s email=%s", user_id, mask_email(user.email or ""))
        except Exception:
            await self.session.rollback()
            raise
