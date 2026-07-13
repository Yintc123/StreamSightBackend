"""extract identity from user

Revision ID: f3e04d5de815
Revises: 062f768dc3bb
Create Date: 2026-07-13 21:29:46.567775

把 users.password_hash 搬到獨立 identities 表、支援未來多種 login provider
(password / Google / GitHub / Apple / ...)。email 改成 nullable(OAuth 用戶可能沒 email)。

Migration steps (idempotent、順序不能反):
    1. 建 identities 表
    2. 把既有 users.password_hash 搬進 identities (provider="password")
    3. Drop users.password_hash 欄位
    4. users.email 改 nullable

Downgrade 反向:email NOT NULL → 加回 password_hash → 搬資料 → drop identities
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3e04d5de815"
down_revision: str | Sequence[str] | None = "062f768dc3bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ─── 1. 建 identities 表 ───
    op.create_table(
        "identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=True),
        sa.Column(
            "credential",
            sa.String(length=255),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "provider", name="uq_identity_user_provider"
        ),
        sa.UniqueConstraint(
            "provider", "provider_user_id", name="uq_identity_provider_sub"
        ),
    )
    op.create_index(
        "ix_identities_user_id", "identities", ["user_id"]
    )
    op.create_index(
        "ix_identities_provider", "identities", ["provider"]
    )

    # ─── 2. 把 users.password_hash 搬進 identities ───
    # 只搬「有 password_hash」的 user (空字串代表 OAuth-only 或還沒設密碼)
    op.execute(
        """
        INSERT INTO identities (
            user_id, provider, provider_user_id, credential, created_at, updated_at
        )
        SELECT id, 'password', NULL, password_hash, created_at, updated_at
        FROM users
        WHERE password_hash IS NOT NULL AND password_hash != ''
        """
    )

    # ─── 3. Drop users.password_hash ───
    op.drop_column("users", "password_hash")

    # ─── 4. users.email 改 nullable ───
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=1024),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # ─── 4 → 1 反向 ───

    # (先加回 password_hash、允許 email NOT NULL 之前才能塞資料)
    op.add_column(
        "users",
        sa.Column(
            "password_hash",
            sa.String(length=255),
            server_default="",
            nullable=False,
        ),
    )

    # 從 identities 搬 password 回 users
    op.execute(
        """
        UPDATE users u
        SET password_hash = i.credential
        FROM identities i
        WHERE i.user_id = u.id AND i.provider = 'password'
        """
    )

    # email 改回 NOT NULL (若有 email=NULL 的 row、此步會失敗、需先清資料)
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=1024),
        nullable=False,
    )

    # drop identities
    op.drop_index("ix_identities_provider", table_name="identities")
    op.drop_index("ix_identities_user_id", table_name="identities")
    op.drop_table("identities")
