"""add_realtime_readings_table

Revision ID: c846f7fdb12a
Revises: c9d0e1f2a3b4
Create Date: 2026-07-19 13:37:29.310408

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c846f7fdb12a"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "realtime_readings",
        sa.Column(
            "id",
            sa.Integer().with_variant(sa.BigInteger(), "mysql"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_realtime_readings_ts", "realtime_readings", ["ts"], unique=False)


def downgrade() -> None:
    op.drop_table("realtime_readings")  # index 隨 table 一起消失（§5.4）
