"""encrypt user email column

Revision ID: e1d9c722650c
Revises: 08211223c402
Create Date: 2026-07-10 10:42:33.186618

Column change is VARCHAR(255) → VARCHAR(512).
Encryption happens at the application (SQLAlchemy TypeDecorator) layer,
so at the SQL level this is just a size increase to fit hex-encoded ciphertext.

⚠️ If any existing rows have plaintext email, they will NOT be encrypted
by this migration — old rows would fail to decrypt on read. In dev the
table should be empty (drop rows first if not).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1d9c722650c'
down_revision: Union[str, Sequence[str], None] = '08211223c402'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'users', 'email',
        existing_type=sa.VARCHAR(length=255),
        type_=sa.String(length=512),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'users', 'email',
        existing_type=sa.String(length=512),
        type_=sa.VARCHAR(length=255),
        existing_nullable=False,
    )
