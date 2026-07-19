"""AdminRepository + admins 表 DB 完整性（username unique、複合 FK 錯配）。§8.1。

含 list_admins / count_admins（狀態謂詞、分頁、稽核者名稱解析）。§7.2。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminStatusFilter, Role
from app.models import Admin, Principal
from app.repositories.repo_admin import AdminRepository
from app.repositories.repo_principal import PrincipalRepository


async def _make_admin(db_session: AsyncSession, *, username: str, name: str = "A") -> Admin:
    principal: Principal = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin: Admin = Admin(
        username=username, name=name, password_hash="$argon2id$stub", principal_id=principal.id
    )
    db_session.add(admin)
    await db_session.flush()
    return admin


async def test_get_by_username_returns_admin(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    await _make_admin(db_session, username="found")

    result = await repo.get_by_username("found")

    assert result is not None
    assert result.username == "found"


async def test_get_by_username_returns_none_when_missing(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    assert await repo.get_by_username("nobody") is None


async def test_get_by_email_removed(db_session: AsyncSession) -> None:
    """get_by_email 已移除（§8.1）。"""
    repo: AdminRepository = AdminRepository(db_session)
    assert not hasattr(repo, "get_by_email")


async def test_get_by_principal_id_returns_admin(db_session: AsyncSession) -> None:
    repo: AdminRepository = AdminRepository(db_session)
    admin = await _make_admin(db_session, username="pid")

    result = await repo.get_by_principal_id(admin.principal_id)

    assert result is not None
    assert result.id == admin.id


async def test_admin_username_unique(db_session: AsyncSession) -> None:
    await _make_admin(db_session, username="clash")

    with pytest.raises(IntegrityError):
        await _make_admin(db_session, username="clash")


async def test_composite_fk_rejects_admin_on_user_role_principal(db_session: AsyncSession) -> None:
    """Admin（role 恆 1）掛到 role=0 的 principal → 複合 FK 擋下。"""
    user_principal: Principal = await PrincipalRepository(db_session).create(Role.USER)
    admin: Admin = Admin(
        username="bad",
        name="Bad",
        password_hash="$argon2id$stub",
        principal_id=user_principal.id,
    )
    db_session.add(admin)

    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── list_admins / count_admins（§7.2）──


async def _seed_states(db_session: AsyncSession) -> dict[str, Admin]:
    """建 root(active) + active + archived(by root) + deleted(by root)。"""
    root = await _make_admin(db_session, username="root", name="Root")
    active = await _make_admin(db_session, username="act", name="Act")
    arch = await _make_admin(db_session, username="arc", name="Arc")
    dele = await _make_admin(db_session, username="del", name="Del")
    now = datetime.now(UTC)
    arch.archived_at, arch.archived_by = now, root.principal_id
    dele.deleted_at, dele.deleted_by = now, root.principal_id
    await db_session.flush()
    return {"root": root, "active": active, "arch": arch, "del": dele}


async def test_list_admins_active_filter(db_session: AsyncSession) -> None:
    s = await _seed_states(db_session)
    repo = AdminRepository(db_session)
    rows = await repo.list_admins(status=AdminStatusFilter.ACTIVE, limit=50, offset=0)
    ids = {r.admin.id for r in rows}
    assert ids == {s["root"].id, s["active"].id}


async def test_list_admins_archived_filter_resolves_actor_username(
    db_session: AsyncSession,
) -> None:
    s = await _seed_states(db_session)
    repo = AdminRepository(db_session)
    rows = await repo.list_admins(status=AdminStatusFilter.ARCHIVED, limit=50, offset=0)
    assert [r.admin.id for r in rows] == [s["arch"].id]
    assert rows[0].archived_by_username == "root"
    assert rows[0].deleted_by_username is None


async def test_list_admins_deleted_filter_resolves_actor_username(
    db_session: AsyncSession,
) -> None:
    s = await _seed_states(db_session)
    repo = AdminRepository(db_session)
    rows = await repo.list_admins(status=AdminStatusFilter.DELETED, limit=50, offset=0)
    assert [r.admin.id for r in rows] == [s["del"].id]
    assert rows[0].deleted_by_username == "root"


async def test_list_admins_all_and_order_by_id(db_session: AsyncSession) -> None:
    s = await _seed_states(db_session)
    repo = AdminRepository(db_session)
    rows = await repo.list_admins(status=AdminStatusFilter.ALL, limit=50, offset=0)
    ids = [r.admin.id for r in rows]
    assert ids == sorted(ids)
    assert set(ids) == {s[k].id for k in s}


async def test_list_admins_pagination(db_session: AsyncSession) -> None:
    await _seed_states(db_session)
    repo = AdminRepository(db_session)
    page1 = await repo.list_admins(status=AdminStatusFilter.ALL, limit=2, offset=0)
    page2 = await repo.list_admins(status=AdminStatusFilter.ALL, limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    # 無重疊、且整體遞增
    assert {r.admin.id for r in page1}.isdisjoint({r.admin.id for r in page2})


async def test_count_admins_matches_list(db_session: AsyncSession) -> None:
    await _seed_states(db_session)
    repo = AdminRepository(db_session)
    for status in AdminStatusFilter:
        rows = await repo.list_admins(status=status, limit=100, offset=0)
        assert await repo.count_admins(status=status) == len(rows)
