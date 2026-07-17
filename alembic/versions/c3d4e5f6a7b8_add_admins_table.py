"""add admins table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-17 00:00:00.000000

新增 admins 表（CMS 管理者），掛在 principals 下（複合 FK + CHECK(role=1)）。
admins 無既有資料，不需回填。見 docs/specs/jwt-role-and-admin.md §3.5 step 3、決策 D4/D5/D9。

Dialect-portable（MariaDB / PostgreSQL）：is_active 的布林 server_default 依方言選字面值
（PostgreSQL 需 `true`；MySQL/MariaDB/SQLite 用 `1`）。email 為 DeterministicEncryptedString →
DB 存密文（VARCHAR），長度 1024 比照 users.email。產出後人工檢視、目標 DB 驗 upgrade/downgrade。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL 布林欄不吃整數 1（需 true）；MySQL/MariaDB/SQLite 用 1
    bool_true: sa.TextClause = (
        sa.text("true") if op.get_bind().dialect.name == "postgresql" else sa.text("1")
    )
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("principal_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("email", sa.String(length=1024), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=bool_true),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["principal_id", "role"],
            ["principals.id", "principals.role"],
            name="fk_admins_principal_role",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("role = 1", name="ck_admins_role_admin"),
    )
    op.create_index("ix_admins_principal_id", "admins", ["principal_id"], unique=True)
    op.create_index("ix_admins_email", "admins", ["email"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_admins_email", table_name="admins")
    op.drop_index("ix_admins_principal_id", table_name="admins")
    op.drop_table("admins")
