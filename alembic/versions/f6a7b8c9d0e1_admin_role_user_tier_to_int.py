"""admin_role / user_tier: StrEnum → IntEnum（String→SmallInteger + 值轉換 + CHECK）

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-19 00:00:00.000000

enum-int.md：AdminRole/UserTier 改 IntEnum（rank = value），欄位由字串轉 int。
- AdminRole：VIEWER=0 / EDITOR=50 / SUPER_ADMIN=100 / ROOT=999（本 migration 建立可容 999 的 CHECK；
  root=999 的實際落地屬 Phase 2 bootstrap）。
- UserTier：FREE=0 / PREMIUM=5。
- **純表示轉換、零語意變更**：`is_protected ⟹ super_admin` 翻譯為 `⟹ 100`（protected→ROOT(999) 的
  轉移屬 Phase 2 bootstrap，非本次）。

upgrade 順序：先 drop 依賴字串值的 CHECK → CASE 轉換資料（'super_admin'→'100'…）→ 改欄型
SmallInteger（MariaDB 自動 cast 數字字串）→ 重建 int CHECK。

測試走 SQLite create_all（model 已 SmallInteger，不經本 migration）；真 MariaDB 驗 upgrade/downgrade。
見 docs/specs/enum-int.md §Migration。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema：admin_role / user_tier 字串 → int。"""
    # 1. drop 依賴字串值的 CHECK（改欄型前必先移除）
    op.drop_constraint("ck_admins_admin_role", "admins", type_="check")
    op.drop_constraint("ck_admins_protected_is_super", "admins", type_="check")
    op.drop_constraint("ck_users_user_tier", "users", type_="check")

    # 2. 資料轉換：字串值 → 對應 int（暫存為數字字串，步驟 3 由 MariaDB cast 成 int）
    op.execute(
        "UPDATE admins SET admin_role = CASE admin_role "
        "WHEN 'viewer' THEN '0' WHEN 'editor' THEN '50' WHEN 'super_admin' THEN '100' "
        "ELSE admin_role END"
    )
    op.execute(
        "UPDATE users SET user_tier = CASE user_tier "
        "WHEN 'free' THEN '0' WHEN 'premium' THEN '5' ELSE user_tier END"
    )

    # 3. 改欄型 String(20) → SmallInteger（MariaDB 自動 cast 數字字串），server_default 改 0
    op.alter_column(
        "admins",
        "admin_role",
        existing_type=sa.String(length=20),
        type_=sa.SmallInteger(),
        existing_nullable=False,
        server_default=sa.text("0"),
        postgresql_using="admin_role::smallint",
    )
    op.alter_column(
        "users",
        "user_tier",
        existing_type=sa.String(length=20),
        type_=sa.SmallInteger(),
        existing_nullable=False,
        server_default=sa.text("0"),
        postgresql_using="user_tier::smallint",
    )

    # 4. 重建 int CHECK（含 ROOT=999 值域；protected ⟹ 100 為純翻譯，Phase 2 改 ⟹999）
    op.create_check_constraint("ck_admins_admin_role", "admins", "admin_role IN (0, 50, 100, 999)")
    op.create_check_constraint(
        "ck_admins_protected_is_super", "admins", "is_protected = 0 OR admin_role = 100"
    )
    op.create_check_constraint("ck_users_user_tier", "users", "user_tier IN (0, 5)")


def downgrade() -> None:
    """Downgrade schema：int → 字串。

    順序鏡射 upgrade：先 drop int CHECK → **先改欄型 int→String**（int 0 → '0'）→ 再把數字
    字串映射回語意字串 → 重建字串 CHECK。**alter 必須先於 UPDATE**：往仍是 SmallInteger 的欄
    寫 'viewer' 會被 MariaDB 擋（error 1366）。（此順序 bug 於真 MariaDB downgrade 才會現形，
    SQLite 測試走 create_all 不經 migration。）
    """
    op.drop_constraint("ck_admins_admin_role", "admins", type_="check")
    op.drop_constraint("ck_admins_protected_is_super", "admins", type_="check")
    op.drop_constraint("ck_users_user_tier", "users", type_="check")

    # 先改欄型 SmallInteger → String（既有 int 值由 DB cast 成數字字串：0→'0'）
    op.alter_column(
        "admins",
        "admin_role",
        existing_type=sa.SmallInteger(),
        type_=sa.String(length=20),
        existing_nullable=False,
        server_default="viewer",
        postgresql_using="admin_role::text",
    )
    op.alter_column(
        "users",
        "user_tier",
        existing_type=sa.SmallInteger(),
        type_=sa.String(length=20),
        existing_nullable=False,
        server_default="free",
        postgresql_using="user_tier::text",
    )

    # 再把數字字串映射回語意字串（999/ROOT 無舊字串對應 → 保守映射為 super_admin）
    op.execute(
        "UPDATE admins SET admin_role = CASE admin_role "
        "WHEN '0' THEN 'viewer' WHEN '50' THEN 'editor' WHEN '100' THEN 'super_admin' "
        "WHEN '999' THEN 'super_admin' ELSE admin_role END"
    )
    op.execute(
        "UPDATE users SET user_tier = CASE user_tier "
        "WHEN '0' THEN 'free' WHEN '5' THEN 'premium' ELSE user_tier END"
    )

    op.create_check_constraint(
        "ck_admins_admin_role", "admins", "admin_role IN ('super_admin', 'editor', 'viewer')"
    )
    op.create_check_constraint(
        "ck_admins_protected_is_super", "admins", "is_protected = 0 OR admin_role = 'super_admin'"
    )
    op.create_check_constraint("ck_users_user_tier", "users", "user_tier IN ('free', 'premium')")
