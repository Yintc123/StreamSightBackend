"""add admins.is_protected + 兩條受保護 root CHECK

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-18 00:00:00.000000

新增 admins.is_protected（受保護 root 標記），把「≥1 super_admin」降為單列不變式。
append-only：接於已部署 head 之後（不就地修訂 c3d4e5f6a7b8，見 model §6.1）。

upgrade 順序有意義——先建欄 → 標記既有 root → 最後才加 CHECK（確保加 CHECK 時所有列
皆已合規）：
  1. ADD COLUMN is_protected（server_default 0 → 既有列自動填 false）。
  2. UPDATE：把現存所有 active super_admin 標為 protected（B，折入 migration）。
     admin 已無 email、無法用 WHERE email 精準指定 bootstrap root，故標記所有 active
     super_admin（允許多個 protected root），消除「ADD COLUMN 後、人工標記前」的不變式空窗。
     fresh 安裝無列，此 UPDATE 為 no-op。
  3. CHECK ck_admins_protected_is_super（protected ⟹ super_admin）。
  4. CHECK ck_admins_protected_is_active（protected ⟹ active）。

布林可攜：server_default 依方言取字面值；MariaDB（TINYINT）/SQLite 皆以 0/1 表達。
測試走 SQLite create_all（不經 migration）；真 MariaDB 驗 upgrade/downgrade。
見 docs/specs/admin-management-model.md §2.3/§6。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "admins",
        sa.Column(
            "is_protected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # 既有安裝：把現存所有 active super_admin 標為受保護 root（B）。
    op.execute(
        "UPDATE admins SET is_protected = 1 "
        "WHERE admin_role = 'super_admin' "
        "AND archived_at IS NULL AND deleted_at IS NULL"
    )
    op.create_check_constraint(
        "ck_admins_protected_is_super",
        "admins",
        "is_protected = 0 OR admin_role = 'super_admin'",
    )
    op.create_check_constraint(
        "ck_admins_protected_is_active",
        "admins",
        "is_protected = 0 OR (archived_at IS NULL AND deleted_at IS NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_admins_protected_is_active", "admins", type_="check")
    op.drop_constraint("ck_admins_protected_is_super", "admins", type_="check")
    op.drop_column("admins", "is_protected")
