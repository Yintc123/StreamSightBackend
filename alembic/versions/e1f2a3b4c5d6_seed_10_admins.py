"""seed_10_admins — 種入 10 位假 admin（開發 / 展示用）

Revision ID: e1f2a3b4c5d6
Revises: c846f7fdb12a
Create Date: 2026-07-19

以 Random(43) 隨機分配 admin_role（VIEWER=0 / EDITOR=50 / SUPER_ADMIN=100，不含 ROOT=999）；
每位 admin 共用一組 seed 密碼，明文記錄於下方供開發測試使用。

seed 密碼（明文）：SeedAdmin#2026!
usernames      ：seed_admin_01 ～ seed_admin_10

downgrade：依 username 精確刪除對應 principals（CASCADE 帶走 admins）。
"""

import random
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "c846f7fdb12a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_USERNAMES = [f"seed_admin_{i:02d}" for i in range(1, 11)]
_SEED_NAMES = [
    "種子管理員甲",
    "種子管理員乙",
    "種子管理員丙",
    "種子管理員丁",
    "種子管理員戊",
    "種子管理員己",
    "種子管理員庚",
    "種子管理員辛",
    "種子管理員壬",
    "種子管理員癸",
]
_ALLOWED_ROLES = [0, 50, 100]  # VIEWER / EDITOR / SUPER_ADMIN（排除 ROOT=999）
_SEED_PASSWORD = "SeedAdmin#2026!"
_SEED_TS = datetime(2026, 7, 19, 0, 0, 0, tzinfo=timezone.utc)


def _compute_roles() -> list[int]:
    rng = random.Random(43)
    return [rng.choice(_ALLOWED_ROLES) for _ in range(10)]


def upgrade() -> None:
    from argon2 import PasswordHasher

    bind = op.get_bind()
    pw_hash = PasswordHasher().hash(_SEED_PASSWORD)
    roles = _compute_roles()
    now = _SEED_TS

    for username, name, admin_role in zip(_SEED_USERNAMES, _SEED_NAMES, roles):
        result = bind.execute(
            sa.text(
                "INSERT INTO principals (role, created_at, updated_at)"
                " VALUES (1, :now, :now)"
            ),
            {"now": now},
        )
        pid: int = result.lastrowid

        bind.execute(
            sa.text(
                "INSERT INTO admins"
                " (principal_id, role, username, name, admin_role,"
                "  is_protected, password_hash, created_at, updated_at)"
                " VALUES (:pid, 1, :username, :name, :admin_role,"
                "         0, :pw_hash, :now, :now)"
            ),
            {
                "pid": pid,
                "username": username,
                "name": name,
                "admin_role": admin_role,
                "pw_hash": pw_hash,
                "now": now,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    for username in _SEED_USERNAMES:
        row = bind.execute(
            sa.text("SELECT principal_id FROM admins WHERE username = :u"),
            {"u": username},
        ).fetchone()
        if row:
            # ondelete="CASCADE" → 刪 principal 連帶移除 admin 列
            bind.execute(
                sa.text("DELETE FROM principals WHERE id = :pid"),
                {"pid": row[0]},
            )
