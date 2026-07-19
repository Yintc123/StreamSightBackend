"""records feature：建立 record_categories（+四筆種子）與 records 表

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-19 00:00:00.000000

records-model.md §6：先建 record_categories 並種入四分類（感測器/系統/應用/網路），
再建 records（其 category_id FK 指向前者 id）。records 本身不種 demo 資料（空表上線）。

- created_by_principal_id FK → admins.principal_id（unique 非 PK 欄）、RESTRICT + NOT NULL（§2.1）
- category_id FK → record_categories.id、RESTRICT（退場走 is_active=False，§2.4）
- title 非空 CHECK（length()，可攜 SQLite/MariaDB，§2.5）
- 顯式 index 只給有 reader 的欄：ix_records_category_id、ix_records_deleted_at（§3.3）；
  created_by_principal_id 不顯式建（無 reader），FK 於 InnoDB 自動索引（對齊 admins audit-FK 慣例）

測試走 SQLite create_all（不經本 migration）；四分類種子由 conftest fixture 注入。真 MariaDB 驗 upgrade/downgrade。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade：record_categories（+seed）→ records。"""
    # 1. record_categories（下拉權威來源；FK 目標須先存在）
    record_categories = op.create_table(
        "record_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_record_categories_name"),
    )
    op.create_index("ix_record_categories_name", "record_categories", ["name"], unique=True)

    # 2. 種入四筆初始分類（對映前端 CATEGORIES 順序；id 自增，records 以 category_id 參照）
    op.bulk_insert(
        record_categories,
        [
            {"name": "感測器", "label": "感測器", "sort_order": 0, "is_active": True},
            {"name": "系統", "label": "系統", "sort_order": 1, "is_active": True},
            {"name": "應用", "label": "應用", "sort_order": 2, "is_active": True},
            {"name": "網路", "label": "網路", "sort_order": 3, "is_active": True},
        ],
    )

    # 3. records（其 category_id FK 指向 record_categories.id）
    op.create_table(
        "records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("created_by_principal_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), server_default=sa.text("''"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["created_by_principal_id"],
            ["admins.principal_id"],
            ondelete="RESTRICT",
            name="fk_records_creator_admin",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["record_categories.id"],
            ondelete="RESTRICT",
            name="fk_records_category",
        ),
        sa.CheckConstraint("length(title) > 0", name="ck_records_title_nonempty"),
    )
    # 只顯式建有 reader 的 index（§3.3）：category（分類篩選/join）、deleted_at（軟刪謂詞）。
    # created_by_principal_id 不顯式建（無 reader）；其 FK 於 InnoDB 會自動索引，對齊
    # admins.archived_by/deleted_by 慣例（那兩欄亦不顯式建 index）。
    op.create_index("ix_records_category_id", "records", ["category_id"])
    op.create_index("ix_records_deleted_at", "records", ["deleted_at"])


def downgrade() -> None:
    """Downgrade：反序 drop records → record_categories（FK 依賴）。

    直接 drop_table（連帶移除其 index/FK）；**不可**先手動 drop_index——MariaDB 會擋
    「FK 仍需該 index」（error 1553）。records 先於 record_categories（前者 FK 依賴後者）。
    """
    op.drop_table("records")
    op.drop_table("record_categories")
