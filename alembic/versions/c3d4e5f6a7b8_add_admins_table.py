"""add admins table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-17 00:00:00.000000

新增 admins 表（CMS 管理者），掛在 principals 下（複合 FK + CHECK(role=1)）。
admins 無既有資料，不需回填。見 docs/specs/admin-account-refinement.md §3.2。

⚠️ 就地修訂（in-place squash）：本表僅存在於未部署的 feature 分支，故直接以最終
schema 建表（username / admin_role / archived_* / deleted_*），不新增 add-then-remove
revision（見規格 §3.2）。username 為非加密明文唯一索引；admin_role 存字串值 + CHECK 硬化，
server_default 'viewer'（最低權限 fail-safe），seed 另以 super_admin 建立。
archived_by / deleted_by 為 nullable FK→principals（ondelete SET NULL）。
產出後人工檢視、目標 DB 驗 upgrade/downgrade。
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
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("principal_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "admin_role", sa.String(length=20), nullable=False, server_default="viewer"
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "archived_by",
            sa.Integer(),
            sa.ForeignKey("principals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "deleted_by",
            sa.Integer(),
            sa.ForeignKey("principals.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            "admin_role IN ('super_admin', 'editor', 'viewer')",
            name="ck_admins_admin_role",
        ),
    )
    op.create_index("ix_admins_principal_id", "admins", ["principal_id"], unique=True)
    op.create_index("ix_admins_username", "admins", ["username"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_admins_username", table_name="admins")
    op.drop_index("ix_admins_principal_id", table_name="admins")
    op.drop_table("admins")
