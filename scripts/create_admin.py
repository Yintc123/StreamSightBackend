"""Seed script：佈建初始 CMS admin（冪等）。

以 `uv run python -m scripts.create_admin` 執行；讀環境變數 INITIAL_ADMIN_USERNAME /
INITIAL_ADMIN_NAME / INITIAL_ADMIN_PASSWORD（見 .env.example）。不進 API、不進 CI 自動跑。
初始 bootstrap admin 以 SUPER_ADMIN 建立（需能管理其他 admin，見規格 §2.7/§4）。
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_app_settings
from app.core.db import AsyncSessionLocal
from app.core.enums import AdminRole
from app.models import Admin
from app.services import AdminService

logger: logging.Logger = logging.getLogger(__name__)


async def create_initial_admin(
    session: AsyncSession, username: str, name: str, password: str
) -> Admin:
    """冪等佈建初始 admin：已存在（以 username 判斷）則回傳現有列、否則以 SUPER_ADMIN 建立。

    Raises:
        ValueError: username / password 為空（未設定 INITIAL_ADMIN_* 環境變數）
    """
    if not username or not password:
        raise ValueError(
            "INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD must be set to seed the initial admin"
        )

    service: AdminService = AdminService(session)
    existing: Admin | None = await service.get_by_username(username)
    if existing is not None:
        logger.info("Initial admin %s already exists; skipping", username)
        return existing

    admin: Admin = await service.create(
        username=username,
        name=name or username,
        password=password,
        admin_role=AdminRole.SUPER_ADMIN,
    )
    logger.info("Created initial admin %s", username)
    return admin


async def main() -> None:
    settings = get_app_settings()
    async with AsyncSessionLocal() as session:
        await create_initial_admin(
            session,
            settings.initial_admin_username,
            settings.initial_admin_name,
            settings.initial_admin_password.get_secret_value(),
        )


if __name__ == "__main__":
    asyncio.run(main())
