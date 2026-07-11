"""rename deleted to is_active

Revision ID: 08211223c402
Revises: 1db6f51d75ab
Create Date: 2026-07-09 17:43:14.441955

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08211223c402'
down_revision: Union[str, Sequence[str], None] = '1db6f51d75ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add is_active with server_default=true (safe for existing rows)
    op.add_column(
        'users',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    # 2. Flip semantics from existing 'deleted' column: is_active = NOT deleted
    op.execute("UPDATE users SET is_active = NOT deleted")
    # 3. Drop the old column
    op.drop_column('users', 'deleted')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'users',
        sa.Column('deleted', sa.BOOLEAN(), nullable=False, server_default=sa.text('false')),
    )
    op.execute("UPDATE users SET deleted = NOT is_active")
    op.drop_column('users', 'is_active')
