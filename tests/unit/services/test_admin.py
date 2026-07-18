"""Unit tests for AdminService — create / get / archive / unarchive / delete(軟刪除) / restore。

見 docs/specs/admin-account-refinement.md §5.3、§8.2。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_password
from app.core.enums import AdminRole, AdminStatusFilter, Role
from app.core.exceptions import (
    BadRequestError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    UnauthorizedError,
)
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


async def test_create_default_is_not_protected(db_session: AsyncSession) -> None:
    """管理 API 一律建立非受保護 admin（is_protected 預設 False）。§3.1。"""
    svc = AdminService(db_session)
    admin = await svc.create(username="plain", name="P", password="longpassword")
    assert admin.is_protected is False


async def test_create_protected_super_admin(db_session: AsyncSession) -> None:
    """seed 建 root：is_protected=True + SUPER_ADMIN（CHECK 相容）。§3.1/§3.7。"""
    svc = AdminService(db_session)
    admin = await svc.create(
        username="root",
        name="Root",
        password="longpassword",
        admin_role=AdminRole.SUPER_ADMIN,
        is_protected=True,
    )
    assert admin.is_protected is True
    assert admin.admin_role == AdminRole.SUPER_ADMIN.value


async def test_create_protected_non_super_raises(db_session: AsyncSession) -> None:
    """is_protected=True 但非 super_admin → CHECK ck_admins_protected_is_super 擋（IntegrityError）。"""
    svc = AdminService(db_session)
    with pytest.raises(IntegrityError):
        await svc.create(
            username="badprot",
            name="B",
            password="longpassword",
            admin_role=AdminRole.EDITOR,
            is_protected=True,
        )


async def test_update_changes_name_and_keeps_token(db_session: AsyncSession) -> None:
    """update 改 name、不撤 token（改名不影響認證）。§3.2/§7.1。"""
    svc = AdminService(db_session)
    admin = await svc.create(username="named", name="Old", password="longpassword")
    await _add_refresh_token(db_session, admin.principal_id)

    updated = await svc.update(admin.id, name="New Name", actor_principal_id=admin.principal_id)

    assert updated.name == "New Name"
    assert await _active_token_count(db_session, admin.principal_id) == 1


async def test_update_soft_deleted_raises_not_found(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="gone", name="G", password="longpassword")
    await svc.delete(admin.id)
    with pytest.raises(NotFoundError):
        await svc.update(admin.id, name="X", actor_principal_id=admin.principal_id)


async def test_change_password_success_revokes_tokens(db_session: AsyncSession) -> None:
    """舊正確 → 換新 hash + 撤全部 refresh token（強制重登）。§3.3/§7.2。"""
    svc = AdminService(db_session)
    admin = await svc.create(username="pwa", name="P", password="oldpassword")
    await _add_refresh_token(db_session, admin.principal_id)

    await svc.change_password(admin.id, current_password="oldpassword", new_password="newpassword1")

    reloaded = await svc.get(admin.id)
    assert await verify_password("newpassword1", reloaded.password_hash)
    assert await _active_token_count(db_session, admin.principal_id) == 0


async def test_change_password_wrong_old_raises_and_no_change(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="pw2", name="P", password="oldpassword")
    old_hash = admin.password_hash

    with pytest.raises(UnauthorizedError):
        await svc.change_password(
            admin.id, current_password="wrongwrong", new_password="newpassword1"
        )
    reloaded = await svc.get(admin.id)
    assert reloaded.password_hash == old_hash


async def test_change_password_new_equals_old_raises(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    admin = await svc.create(username="pw3", name="P", password="samepassword")
    with pytest.raises(BadRequestError):
        await svc.change_password(
            admin.id, current_password="samepassword", new_password="samepassword"
        )


# ── set_admin_role 守衛（§3.4/§7.3）──


async def _mk(
    svc: AdminService, username: str, role: AdminRole = AdminRole.VIEWER, *, protected: bool = False
) -> Admin:
    return await svc.create(
        username=username,
        name=username,
        password="longpassword",
        admin_role=AdminRole.SUPER_ADMIN if protected else role,
        is_protected=protected,
    )


async def test_set_admin_role_promote_by_other_keeps_token(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    target = await _mk(svc, "tgt", AdminRole.EDITOR)
    actor = await _mk(svc, "act", AdminRole.SUPER_ADMIN)
    await _add_refresh_token(db_session, target.principal_id)

    updated = await svc.set_admin_role(
        target.id, admin_role=AdminRole.SUPER_ADMIN, actor_principal_id=actor.principal_id
    )
    assert updated.admin_role == AdminRole.SUPER_ADMIN.value
    assert await _active_token_count(db_session, target.principal_id) == 1  # 不撤 token


async def test_set_admin_role_demote_non_protected_super(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    target = await _mk(svc, "sup", AdminRole.SUPER_ADMIN)  # 非受保護
    actor = await _mk(svc, "act", AdminRole.SUPER_ADMIN)
    updated = await svc.set_admin_role(
        target.id, admin_role=AdminRole.EDITOR, actor_principal_id=actor.principal_id
    )
    assert updated.admin_role == AdminRole.EDITOR.value  # root 仍在，無需計數


async def test_set_admin_role_demote_protected_root_raises(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    root = await _mk(svc, "root", protected=True)
    actor = await _mk(svc, "act", AdminRole.SUPER_ADMIN)
    with pytest.raises(BusinessRuleError):
        await svc.set_admin_role(
            root.id, admin_role=AdminRole.EDITOR, actor_principal_id=actor.principal_id
        )


async def test_set_admin_role_self_elevation_raises(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    me = await _mk(svc, "mee", AdminRole.VIEWER)
    with pytest.raises(BusinessRuleError):
        await svc.set_admin_role(
            me.id, admin_role=AdminRole.SUPER_ADMIN, actor_principal_id=me.principal_id
        )


async def test_set_admin_role_idempotent_same_level(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    target = await _mk(svc, "same", AdminRole.EDITOR)
    updated = await svc.set_admin_role(
        target.id, admin_role=AdminRole.EDITOR, actor_principal_id=target.principal_id
    )
    assert updated.admin_role == AdminRole.EDITOR.value  # 等級未變、無自我提權誤擋


async def test_set_admin_role_idempotent_protected_root_super(db_session: AsyncSession) -> None:
    """H2：對受保護 root 設回 super_admin（同級）→ idempotent 成功、不被守衛誤擋。"""
    svc = AdminService(db_session)
    root = await _mk(svc, "root", protected=True)
    actor = await _mk(svc, "act", AdminRole.SUPER_ADMIN)
    updated = await svc.set_admin_role(
        root.id, admin_role=AdminRole.SUPER_ADMIN, actor_principal_id=actor.principal_id
    )
    assert updated.admin_role == AdminRole.SUPER_ADMIN.value


# ── archive / delete 守衛（§3.5/§3.6/§7.4）──


async def test_archive_protected_root_raises(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    root = await _mk(svc, "root", protected=True)
    actor = await _mk(svc, "act", AdminRole.SUPER_ADMIN)
    with pytest.raises(BusinessRuleError):
        await svc.archive(root.id, actor_principal_id=actor.principal_id)


async def test_delete_protected_root_raises(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    root = await _mk(svc, "root", protected=True)
    with pytest.raises(BusinessRuleError):
        await svc.delete(root.id, actor_principal_id=None)  # M3：actor=None 也擋受保護


async def test_archive_super_admin_requires_demote(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    sup = await _mk(svc, "sup", AdminRole.SUPER_ADMIN)  # 非受保護
    actor = await _mk(svc, "act", AdminRole.SUPER_ADMIN)
    with pytest.raises(BusinessRuleError):
        await svc.archive(sup.id, actor_principal_id=actor.principal_id)


async def test_delete_super_admin_requires_demote_even_actor_none(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    sup = await _mk(svc, "sup", AdminRole.SUPER_ADMIN)
    with pytest.raises(BusinessRuleError):
        await svc.delete(sup.id, actor_principal_id=None)  # M3：super_admin 守衛恆適用


async def test_two_step_demote_then_delete_succeeds(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    sup = await _mk(svc, "sup", AdminRole.SUPER_ADMIN)
    actor = await _mk(svc, "act", AdminRole.SUPER_ADMIN)
    await svc.set_admin_role(
        sup.id, admin_role=AdminRole.VIEWER, actor_principal_id=actor.principal_id
    )
    await svc.delete(sup.id, actor_principal_id=actor.principal_id)
    with pytest.raises(NotFoundError):
        await svc.get(sup.id)  # 已軟刪除


async def test_archive_self_raises(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    ed = await _mk(svc, "edd", AdminRole.EDITOR)
    with pytest.raises(BusinessRuleError):
        await svc.archive(ed.id, actor_principal_id=ed.principal_id)


async def test_delete_self_raises(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    ed = await _mk(svc, "edd", AdminRole.EDITOR)
    with pytest.raises(BusinessRuleError):
        await svc.delete(ed.id, actor_principal_id=ed.principal_id)


async def test_archive_editor_by_script_actor_none_succeeds(db_session: AsyncSession) -> None:
    """M3：actor=None（script）→ 自我守衛不適用；editor 非受保護非 super → 成功。"""
    svc = AdminService(db_session)
    ed = await _mk(svc, "edd", AdminRole.EDITOR)
    archived = await svc.archive(ed.id, actor_principal_id=None)
    assert archived.archived_at is not None


async def test_archive_already_archived_editor_idempotent(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    ed = await _mk(svc, "edd", AdminRole.EDITOR)
    actor = await _mk(svc, "act", AdminRole.SUPER_ADMIN)
    first = await svc.archive(ed.id, actor_principal_id=actor.principal_id)
    again = await svc.archive(ed.id, actor_principal_id=actor.principal_id)  # idempotent
    assert again.id == first.id
    assert again.archived_at == first.archived_at


async def test_db_backstop_direct_archive_protected_raises(db_session: AsyncSession) -> None:
    """DB 兜底（A）：繞過 service 直接對受保護 root 設 archived_at → IntegrityError。"""
    svc = AdminService(db_session)
    root = await _mk(svc, "root", protected=True)
    root.archived_at = datetime.now(UTC)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_list_admins_delegates_returns_rows_and_total(db_session: AsyncSession) -> None:
    """§3.8：委派 repo.list_admins + count_admins，回 (rows, total)。"""
    svc = AdminService(db_session)
    await svc.create(username="aaa", name="A", password="longpassword")
    await svc.create(username="bbb", name="B", password="longpassword")

    rows, total = await svc.list_admins(status=AdminStatusFilter.ACTIVE, limit=50, offset=0)
    assert total == 2
    assert {r.admin.username for r in rows} == {"aaa", "bbb"}


async def test_list_admins_defaults_to_active(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    ed = await svc.create(username="ccc", name="C", password="longpassword")
    await svc.delete(ed.id)  # 軟刪除 → 不在 active
    rows, total = await svc.list_admins()
    assert total == 0
    assert rows == []


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
