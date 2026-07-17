"""Unit tests for AdminService — create / get / archive / unarchive / delete(軟刪除) / restore。

見 docs/specs/admin-account-refinement.md §5.3、§8.2。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole, Role
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models import Admin, Principal, RefreshToken
from app.repositories.principal import PrincipalRepository
from app.services.admin import AdminService


async def _add_refresh_token(
    db_session: AsyncSession, principal_id: int, *, family_id: str = "fam", hash_: str = "h"
) -> RefreshToken:
    token = RefreshToken(
        principal_id=principal_id,
        token_hash=hash_,
        family_id=family_id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(token)
    await db_session.commit()
    return token


async def _active_token_count(db_session: AsyncSession, principal_id: int) -> int:
    rows = (
        (
            await db_session.execute(
                select(RefreshToken).where(
                    RefreshToken.principal_id == principal_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


# ─────────────────────────── create ───────────────────────────
async def test_create_builds_principal_role1_and_hashes_password(
    db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)

    admin = await svc.create(username="root", name="Root", password="longpassword")

    assert admin.id is not None
    assert admin.role == 1
    assert admin.username == "root"
    assert admin.password_hash.startswith("$argon2id$")
    principal = await PrincipalRepository(db_session).get(admin.principal_id)
    assert principal is not None
    assert principal.role == 1


async def test_create_normalizes_username_lowercase(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="  Root ", name="Root", password="longpassword")
    assert admin.username == "root"


async def test_create_duplicate_username_case_variant_raises_conflict_no_orphan(
    db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)
    await svc.create(username="dup", name="D", password="longpassword")

    with pytest.raises(ConflictError):
        await svc.create(username="DUP", name="D2", password="longpassword")

    # 不留孤兒 principal：僅一個 admin principal 存在
    principals = (
        (await db_session.execute(select(Principal).where(Principal.role == Role.ADMIN.value)))
        .scalars()
        .all()
    )
    assert len(principals) == 1


# 格式驗證於「正規化後」的值上（§2.1）：strip+lower 後仍不符 _USERNAME_RE 才擋。
# （"Root" → "root"、"no_UPPER" → "no_upper" 正規化後合法，故不在此列。）
@pytest.mark.parametrize("bad", ["ab", "has space", "a@b", "bad/slash", ""])
async def test_create_invalid_username_format_raises_bad_request_no_rows(
    db_session: AsyncSession, bad: str
) -> None:
    svc = AdminService(db_session)
    with pytest.raises(BadRequestError):
        await svc.create(username=bad, name="X", password="longpassword")

    admins = (await db_session.execute(select(Admin))).scalars().all()
    assert admins == []


async def test_create_admin_role_defaults_viewer(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="viewerdefault", name="V", password="longpassword")
    assert admin.admin_role == AdminRole.VIEWER.value


async def test_create_admin_role_explicit_super_admin(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(
        username="boss", name="B", password="longpassword", admin_role=AdminRole.SUPER_ADMIN
    )
    assert admin.admin_role == AdminRole.SUPER_ADMIN.value


async def test_get_by_username_and_principal_id(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    created = await svc.create(username="lookup", name="L", password="longpassword")

    assert (await svc.get_by_username("lookup")).id == created.id  # type: ignore[union-attr]
    assert (await svc.get_by_principal_id(created.principal_id)).id == created.id  # type: ignore[union-attr]


# ─────────────────────────── get / include_deleted ───────────────────────────
async def test_get_soft_deleted_raises_notfound_but_include_deleted_returns(
    db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="gone", name="G", password="longpassword")
    await svc.delete(admin.id)

    with pytest.raises(NotFoundError):
        await svc.get(admin.id)
    recovered = await svc.get(admin.id, include_deleted=True)
    assert recovered.id == admin.id


# ─────────────────────────── archive ───────────────────────────
async def test_archive_sets_timestamp_deactivates_and_revokes_tokens(
    db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="arch", name="A", password="longpassword")
    await _add_refresh_token(db_session, admin.principal_id)

    archived = await svc.archive(admin.id)

    assert archived.archived_at is not None
    assert archived.is_active is False
    assert await _active_token_count(db_session, admin.principal_id) == 0


async def test_archive_idempotent(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="arch2", name="A", password="longpassword")
    first = await svc.archive(admin.id)
    ts = first.archived_at
    second = await svc.archive(admin.id)
    assert second.archived_at == ts


async def test_archive_records_actor(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    actor_principal = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin = await svc.create(username="arch3", name="A", password="longpassword")

    archived = await svc.archive(admin.id, actor_principal_id=actor_principal.id)
    assert archived.archived_by == actor_principal.id


async def test_archive_without_actor_leaves_by_null(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="arch4", name="A", password="longpassword")
    archived = await svc.archive(admin.id)
    assert archived.archived_by is None


# ─────────────────────────── unarchive ───────────────────────────
async def test_unarchive_clears_pair_and_reactivates(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    actor = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin = await svc.create(username="unarch", name="A", password="longpassword")
    await svc.archive(admin.id, actor_principal_id=actor.id)

    restored = await svc.unarchive(admin.id)

    assert restored.archived_at is None
    assert restored.archived_by is None
    assert restored.is_active is True


# ─────────────────────────── delete (soft) ───────────────────────────
async def test_delete_is_soft_deactivates_revokes_and_keeps_rows(
    db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="del", name="D", password="longpassword")
    await _add_refresh_token(db_session, admin.principal_id)

    await svc.delete(admin.id)

    admin_row = (
        await db_session.execute(select(Admin).where(Admin.id == admin.id))
    ).scalar_one_or_none()
    principal_row = (
        await db_session.execute(select(Principal).where(Principal.id == admin.principal_id))
    ).scalar_one_or_none()
    assert admin_row is not None
    assert admin_row.deleted_at is not None
    assert admin_row.is_active is False
    assert principal_row is not None  # 軟刪除，不刪 principals
    assert await _active_token_count(db_session, admin.principal_id) == 0


async def test_delete_records_actor(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    actor = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin = await svc.create(username="del2", name="D", password="longpassword")

    await svc.delete(admin.id, actor_principal_id=actor.id)

    row = await svc.get(admin.id, include_deleted=True)
    assert row.deleted_by == actor.id


async def test_delete_already_deleted_raises_notfound(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="del3", name="D", password="longpassword")
    await svc.delete(admin.id)

    with pytest.raises(NotFoundError):
        await svc.delete(admin.id)


# ─────────────────────────── restore ───────────────────────────
async def test_restore_clears_pair_reactivates_and_gettable(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    actor = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin = await svc.create(username="restore", name="R", password="longpassword")
    await svc.delete(admin.id, actor_principal_id=actor.id)

    restored = await svc.restore(admin.id)

    assert restored.deleted_at is None
    assert restored.deleted_by is None
    assert restored.is_active is True
    assert (await svc.get(admin.id)).id == admin.id  # 預設 get 可取回
    # 同 username 仍為該列（未被佔用）
    assert (await svc.get_by_username("restore")).id == admin.id  # type: ignore[union-attr]


async def test_restore_non_deleted_idempotent(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="restore2", name="R", password="longpassword")
    restored = await svc.restore(admin.id)
    assert restored.deleted_at is None


# ─────────────────────────── audit pair invariant ───────────────────────────
async def test_audit_pair_invariant_across_transitions(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    actor = await PrincipalRepository(db_session).create(Role.ADMIN)
    admin = await svc.create(username="invariant", name="I", password="longpassword")

    def _check(a: Admin) -> None:
        assert (a.archived_at is None) == (a.archived_by is None)
        assert (a.deleted_at is None) == (a.deleted_by is None)

    _check(admin)
    _check(await svc.archive(admin.id, actor_principal_id=actor.id))
    _check(await svc.unarchive(admin.id))
    _check(
        await svc.delete(admin.id, actor_principal_id=actor.id)
        or await svc.get(admin.id, include_deleted=True)
    )
    _check(await svc.restore(admin.id))


# ─────────────────────── isolation：不波及其他 principal ───────────────────────
async def test_archive_does_not_affect_other_principals_tokens(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    a = await svc.create(username="targeta", name="A", password="longpassword")
    b = await svc.create(username="othera", name="B", password="longpassword")
    await _add_refresh_token(db_session, a.principal_id, family_id="fa", hash_="ha")
    await _add_refresh_token(db_session, b.principal_id, family_id="fb", hash_="hb")

    await svc.archive(a.id)

    assert await _active_token_count(db_session, a.principal_id) == 0
    assert await _active_token_count(db_session, b.principal_id) == 1
