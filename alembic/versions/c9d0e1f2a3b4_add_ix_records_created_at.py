"""add ix_records_created_at

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-19

records-model.md §3.3：新增 ix_records_created_at 供日期範圍謂詞（created_at >= / <）與
created_at 排序使用（§2.7-(2)，05-analytics 頁時間篩選需求）。
"""

from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_records_created_at", "records", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_records_created_at", table_name="records")
