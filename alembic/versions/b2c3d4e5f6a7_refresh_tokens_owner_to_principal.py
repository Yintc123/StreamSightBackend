"""refresh_tokens owner: user_id -> principal_id

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-17 00:00:00.000000

把 refresh_tokens 的擁有者由 user_id（FK→users）換成 principal_id（FK→principals，CASCADE），
讓 users / admins 共用同一套 rotation / reuse / logout-all。見 docs/specs/jwt-role-and-admin.md §3.5 step 4。

Dialect-portable（MariaDB / PostgreSQL；資料 SQL 亦相容 SQLite）：
    - 回填用「相關子查詢」而非 MySQL 專屬的 UPDATE..JOIN 或 PostgreSQL 專屬的 UPDATE..FROM，
      `UPDATE t SET x = (SELECT ... WHERE ... = t.fk)` 三種 DB 皆合法。
    - DROP FK 用 inspector 取實際名（各 DB 自動命名不同，如 MariaDB refresh_tokens_ibfk_1、
      PostgreSQL refresh_tokens_user_id_fkey）。
產出後需人工檢視、在目標 DB 驗 upgrade/downgrade。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk_name(table: str, referred_table: str) -> str | None:
    """以 inspector 取得 table→referred_table 的實際 FK 名（勿寫死 MariaDB 自動名）。"""
    inspector = sa.inspect(op.get_bind())
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") == referred_table:
            return fk.get("name")
    return None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. 加 principal_id（先 NULL 供回填）
    op.add_column("refresh_tokens", sa.Column("principal_id", sa.Integer(), nullable=True))
    # 2. 回填：接到對應 user 的 principal（相關子查詢，跨 DB 可攜）
    op.execute(
        "UPDATE refresh_tokens SET principal_id = "
        "(SELECT u.principal_id FROM users u WHERE u.id = refresh_tokens.user_id)"
    )
    # 3. 拆掉舊 user_id 的 FK / index / 欄位
    fk_name: str | None = _fk_name("refresh_tokens", "users")
    if fk_name:
        op.drop_constraint(fk_name, "refresh_tokens", type_="foreignkey")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "user_id")
    # 4. principal_id 收緊：NOT NULL + FK(CASCADE) + index
    op.alter_column(
        "refresh_tokens", "principal_id", existing_type=sa.Integer(), nullable=False
    )
    op.create_foreign_key(
        "fk_refresh_tokens_principal",
        "refresh_tokens",
        "principals",
        ["principal_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_refresh_tokens_principal_id", "refresh_tokens", ["principal_id"]
    )


def downgrade() -> None:
    """Downgrade schema（還原為 user_id 擁有者）。"""
    op.add_column("refresh_tokens", sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE refresh_tokens SET user_id = "
        "(SELECT u.id FROM users u WHERE u.principal_id = refresh_tokens.principal_id)"
    )
    # ⚠️ 先 drop FK 再 drop index：MariaDB/InnoDB 要求 FK 必有可用的 backing index，
    # 若先 drop index 會報 (1553, "Cannot drop index ... needed in a foreign key constraint")。
    # 順序須與 upgrade() 拆 user_id 時一致（先 FK 後 index）。
    op.drop_constraint("fk_refresh_tokens_principal", "refresh_tokens", type_="foreignkey")
    op.drop_index("ix_refresh_tokens_principal_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "principal_id")
    op.alter_column("refresh_tokens", "user_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "fk_refresh_tokens_user_id",
        "refresh_tokens",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
