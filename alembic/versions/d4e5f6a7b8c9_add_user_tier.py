"""add users.user_tier

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-18 00:00:00.000000

新增 users.user_tier（一般 User 等級，供 rbac 授權與 grade claim）。
append-only：接於已部署 head c3d4e5f6a7b8 之後（不就地修訂）。
server_default 'free'（最低權限 fail-safe）→ 既有列自動回填 FREE，無需另行 UPDATE。
值域以 CHECK 硬化（對齊 ck_admins_admin_role 風格）。
見 docs/specs/rbac.md §3.2/§3.3。產出後人工檢視、真 MariaDB 驗 upgrade/downgrade。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "user_tier",
            sa.String(length=20),
            nullable=False,
            server_default="free",
        ),
    )
    op.create_check_constraint(
        "ck_users_user_tier",
        "users",
        "user_tier IN ('free', 'premium')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_users_user_tier", "users", type_="check")
    op.drop_column("users", "user_tier")
