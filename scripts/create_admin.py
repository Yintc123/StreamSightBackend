"""Seed script：佈建初始 CMS admin（冪等）。

以 `uv run python -m scripts.create_admin` 執行；讀環境變數 INITIAL_ADMIN_EMAIL /
INITIAL_ADMIN_PASSWORD（見 .env.example）。不進 API、不進 CI 自動跑。見規格 §5.8、決策 D7。
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_app_settings
from app.core.db import AsyncSessionLocal
from app.models import Admin
from app.services import AdminService

logger: logging.Logger = logging.getLogger(__name__)


async def create_initial_admin(session: AsyncSession, email: str, password: str) -> Admin:
    """冪等佈建初始 admin：已存在則回傳現有列、否則建立。

    Raises:
        ValueError: email / password 為空（未設定 INITIAL_ADMIN_* 環境變數）
    """
    if not email or not password:
        raise ValueError(
            "INITIAL_ADMIN_EMAIL / INITIAL_ADMIN_PASSWORD must be set to seed the initial admin"
        )

    service: AdminService = AdminService(session)
    existing: Admin | None = await service.get_by_email(email)
    if existing is not None:
        logger.info("Initial admin %s already exists; skipping", email)
        return existing

    admin: Admin = await service.create(email=email, name="admin", password=password)
    logger.info("Created initial admin %s", email)
    return admin


async def main() -> None:
    settings = get_app_settings()
    async with AsyncSessionLocal() as session:
        await create_initial_admin(
            session,
            settings.initial_admin_email,
            settings.initial_admin_password.get_secret_value(),
        )


if __name__ == "__main__":
    asyncio.run(main())
