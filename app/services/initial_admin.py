"""Bootstrap root admin — 開機時 upsert 成**真實 DB admin**（bootstrap-hidden-admin.md）。

三個 env（`INITIAL_ADMIN_USERNAME` / `_NAME` / `_PASSWORD`，皆必填）→ lifespan 啟動時呼叫
`ensure_initial_admin`：無任何 protected root 時，建一筆 `admin_role=ROOT`、`is_protected=True`
的真實列（seed-once、冪等鍵＝有無 protected root）。密碼由 env 明文於啟動時 hash 後寫入 DB。

登入 / 授權 / 改密碼 / 稽核全走一般路徑——**無哨兵、無 synthetic Admin、無 `sub==0` 特判**。
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.password import hash_password
from app.core.config import get_app_settings
from app.core.enums import AdminRole, Role
from app.core.security import (
    MAX_NAME_LEN,
    normalize_username,
    validate_admin_password,
    validate_admin_username,
)
from app.models.admin import Admin
from app.repositories.admin import AdminRepository
from app.repositories.principal import PrincipalRepository

logger = logging.getLogger(__name__)


def initial_admin_enabled() -> bool:
    """env 是否設好 bootstrap root（username + password 皆非空）。"""
    s = get_app_settings()
    return bool(s.initial_admin_username and s.initial_admin_password.get_secret_value())


def is_initial_admin_username(username: str) -> bool:
    """已設定且 username（正規化）命中 bootstrap root（供 create 保留字檢查）。"""
    s = get_app_settings()
    return bool(s.initial_admin_username) and username == normalize_username(
        s.initial_admin_username
    )


def _validate_admin_fields(username: str, password: str, name: str) -> None:
    """bootstrap admin 受與一般 admin 相同的欄位政策；不符 → `RuntimeError`（啟動 fail-fast）。

    政策單一真相在 `app/core/security.py`（DTO 與此共用）。
    """
    try:
        validate_admin_username(username)
        validate_admin_password(password)
    except ValueError as e:
        raise RuntimeError(f"initial admin config invalid: {e}") from e
    if not (1 <= len(name) <= MAX_NAME_LEN):
        raise RuntimeError(f"INITIAL_ADMIN_NAME must be 1-{MAX_NAME_LEN} chars")


async def ensure_initial_admin(session: AsyncSession) -> None:
    """開機 upsert bootstrap root：三 env 必填 + 政策驗證（fail-fast）→ 無 root 才建（seed-once）。

    Raises:
        RuntimeError: 任一 env 缺（含皆空、半套）或欄位不合法（§3.3）。
    """
    s = get_app_settings()
    u = normalize_username(s.initial_admin_username)
    pw = s.initial_admin_password.get_secret_value()
    name = s.initial_admin_name
    if not u or not pw or not name:  # 三者皆必需：任一為空 → fail-fast
        raise RuntimeError(
            "INITIAL_ADMIN_USERNAME, INITIAL_ADMIN_PASSWORD, INITIAL_ADMIN_NAME are required — "
            "app cannot start without admin credentials"
        )
    _validate_admin_fields(u, pw, name)  # 非空但不合法 → fail-fast

    admin_repo = AdminRepository(session)
    if await admin_repo.protected_root_exists():  # 冪等鍵：已有任何 root 就跳過
        return
    pw_hash = await hash_password(pw)
    try:
        principal = await PrincipalRepository(session).create(Role.ADMIN)  # id 自增
        session.add(
            Admin(
                principal_id=principal.id,
                username=u,
                name=name,
                admin_role=AdminRole.ROOT.value,
                is_protected=True,
                password_hash=pw_hash,
            )
        )
        await session.commit()  # principal + admin 原子落地
        logger.info("Seeded bootstrap root admin username=%s", u)
    except IntegrityError:  # 併發輸家：另一 worker 已建
        await session.rollback()
