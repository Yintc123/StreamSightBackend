"""add principals supertype + attach users via composite FK (preserve id)

Revision ID: a1b2c3d4e5f6
Revises: 9f3c1a4b2d7e
Create Date: 2026-07-17 00:00:00.000000

新增 principals 父表，並把既有 users 一對一掛上（複合 FK 硬化型別-角色一致性）。
見 docs/specs/jwt-role-and-admin.md §3.5 step 1-2、決策 D1/D9。

⚠️ 保留 id（`principal.id == user.id`）：既有 user 的 principal_id 直接沿用 user.id，
   舊 access token 的 sub 仍解析到同一 user → cutover 不需輪替 JWT_SECRET_KEY（見 §7/D8）。

Dialect-portable（MariaDB / PostgreSQL；資料 SQL 亦相容 SQLite）：
    - 回填用可攜 INSERT..SELECT / UPDATE。
    - 保留 id 後「序列前進」：PostgreSQL 的 sequence 不會因顯式 id 前進，需 setval；
      MySQL/MariaDB(InnoDB) 與 SQLite 會自動把計數器推到 max(id)+1，無需處理。
產出後需人工檢視，並在目標 DB 跑 upgrade/downgrade 驗證（測試走 SQLite create_all，不經 migration）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "9f3c1a4b2d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _advance_pk_sequence(table: str, column: str) -> None:
    """讓自增 PK 計數器跳過「顯式插入的 id」，使後續新列從 max(id)+1 取號。

    PostgreSQL：顯式 id 不會前進 sequence → 用 setval 校正（否則下一筆撞 PK）。
    MySQL/MariaDB(InnoDB) 與 SQLite：插入顯式 id 後計數器自動前進，無需處理。
    """
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), "
            f"(SELECT COALESCE(MAX({column}), 1) FROM {table}))"
        )


def upgrade() -> None:
    """Upgrade schema."""
    # 1. principals 父表（只承載判別子 role；UNIQUE(id, role) 供 child 複合 FK 參照）
    op.create_table(
        "principals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "role", name="uq_principals_id_role"),
        sa.CheckConstraint("role IN (0, 1)", name="ck_principals_role_domain"),
    )

    # 2. users 加 principal_id（先 NULL 供回填）+ 常數 role 欄
    op.add_column("users", sa.Column("principal_id", sa.Integer(), nullable=True))
    op.add_column(
        "users",
        sa.Column("role", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )

    # 3. 回填（保留 id）：principal.id == user.id，principal_id 直接等於自己的 id
    op.execute(
        "INSERT INTO principals (id, role, created_at, updated_at) "
        "SELECT id, 0, created_at, updated_at FROM users"
    )
    op.execute("UPDATE users SET principal_id = id")
    # 保留 id 後校正自增計數器，讓新 admin/user 的 principal 從 max(id)+1 取號（見 §7）
    _advance_pk_sequence("principals", "id")

    # 4. principal_id 收緊為 NOT NULL + UNIQUE + index
    op.alter_column("users", "principal_id", existing_type=sa.Integer(), nullable=False)
    op.create_index("ix_users_principal_id", "users", ["principal_id"], unique=True)

    # 5. 複合 FK（型別-角色一致性硬化）+ CHECK(role=0) 釘死常數
    op.create_foreign_key(
        "fk_users_principal_role",
        "users",
        "principals",
        ["principal_id", "role"],
        ["id", "role"],
        ondelete="CASCADE",
    )
    op.create_check_constraint("ck_users_role_user", "users", "role = 0")


def downgrade() -> None:
    """Downgrade schema（僅需還原 role 0 的 user）。"""
    op.drop_constraint("ck_users_role_user", "users", type_="check")
    op.drop_constraint("fk_users_principal_role", "users", type_="foreignkey")
    op.drop_index("ix_users_principal_id", table_name="users")
    op.drop_column("users", "role")
    op.drop_column("users", "principal_id")
    op.drop_table("principals")
