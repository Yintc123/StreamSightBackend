"""add refresh_tokens table

Revision ID: 9f3c1a4b2d7e
Revises: f3e04d5de815
Create Date: 2026-07-17 00:00:00.000000

新增 refresh_tokens 表，支援 opaque refresh token + rotation + reuse detection。
    - token_hash：HMAC-SHA256(pepper, token) 的 hex digest（unique，查詢主鍵）
    - family_id：同一登入 session 的輪替鏈共用（reuse detection 連坐用）
    - revoked_at：NULL = active；rotation / logout / reuse 撤銷都寫這裡
    - replaced_by_id：自參考 FK，串起輪替鏈（audit），ON DELETE SET NULL

手動撰寫（本機無 DB 可 autogenerate），欄位與 app/models/refresh_token.py 對齊。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f3c1a4b2d7e"
down_revision: str | Sequence[str] | None = "f3e04d5de815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "replaced_by_id",
            sa.Integer(),
            sa.ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
            nullable=True,
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
    )
    op.create_index(
        "ix_refresh_tokens_token_hash",
        "refresh_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    """Downgrade schema."""
    # drop_table 會一併移除該表自身的 index 與 FK（三方言皆然）；不先 drop_index。
    # ⚠️ MariaDB/InnoDB 下，ix_refresh_tokens_user_id 是 user_id FK 的 backing index，
    # 若先 drop_index 會報 (1553, "... needed in a foreign key constraint")。直接 drop_table 最單純可靠。
    op.drop_table("refresh_tokens")
