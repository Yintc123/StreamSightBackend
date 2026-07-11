"""resize user email column 512 to 1024

Revision ID: 290ef626bb55
Revises: e1d9c722650c
Create Date: 2026-07-11 13:33:14.117205

Column change is VARCHAR(512) → VARCHAR(1024).
只擴充上限，SQL 層行為不變、資料無須遷移（既存 ciphertext 仍在 512 以內）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '290ef626bb55'
down_revision: Union[str, Sequence[str], None] = 'e1d9c722650c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'users', 'email',
        existing_type=sa.String(length=512),
        type_=sa.String(length=1024),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'users', 'email',
        existing_type=sa.String(length=1024),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
