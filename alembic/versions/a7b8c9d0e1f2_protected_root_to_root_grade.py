"""bootstrap: protected root CHECK `is_protected⟹super_admin(100)` → `⟹ROOT(999)`

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-19 00:00:00.000000

bootstrap-hidden-admin.md §2.1：root 落地為真實 DB 列、grade 由 SUPER_ADMIN 提升為 ROOT(999)。
enum-int（Phase 1）已把此 CHECK 純翻譯為 `⟹100`；本 migration 做 protected→ROOT 的語意轉移。

seed root 之前既有無 `is_protected=True` 列（哨兵不落地）→ 改 CHECK 不擋既有資料；改完由
`ensure_initial_admin`（startup upsert）建 root(999, protected)。見 docs/specs/bootstrap-hidden-admin.md。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade：protected ⟹ super_admin(100) → ⟹ ROOT(999)。"""
    op.drop_constraint("ck_admins_protected_is_super", "admins", type_="check")
    op.create_check_constraint(
        "ck_admins_protected_is_super", "admins", "is_protected = 0 OR admin_role = 999"
    )


def downgrade() -> None:
    """Downgrade：protected ⟹ ROOT(999) → ⟹ super_admin(100)。"""
    op.drop_constraint("ck_admins_protected_is_super", "admins", type_="check")
    op.create_check_constraint(
        "ck_admins_protected_is_super", "admins", "is_protected = 0 OR admin_role = 100"
    )
