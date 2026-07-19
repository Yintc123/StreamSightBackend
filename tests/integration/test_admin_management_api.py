"""Integration tests for Admin 管理 API（/admin/admins/* + /admin/me/password）。

admin-management-api.md §8。沿用 `admin` fixture（super_admin）；viewer/editor 以
AdminService.create 佈局；受保護 root 以 create(is_protected=True)。
"""

from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole
from app.models import Admin
from app.services import AdminService
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _login(
    client: AsyncClient, username: str = ADMIN_USERNAME, pw: str = ADMIN_PASSWORD
) -> str:
    resp = await client.post("/admin/auth/login", json={"username": username, "password": pw})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _mk(
    db: AsyncSession, username: str, role: AdminRole, *, pw: str = "longpassword"
) -> Admin:
    return await AdminService(db).create(
        username=username, name=username, password=pw, admin_role=role
    )


# ── §8.1 授權 ──


async def test_lower_roles_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    await _mk(db_session, "vwr", AdminRole.VIEWER)
    token = await _login(client, "vwr", "longpassword")
    resp = await client.get("/admin/admins", headers=_auth(token))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_super_admin_allowed(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    resp = await client.get("/admin/admins", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK


async def test_no_token_401(client: AsyncClient) -> None:
    assert (await client.get("/admin/admins")).status_code == status.HTTP_401_UNAUTHORIZED


async def test_user_token_forbidden(client: AsyncClient) -> None:
    reg = await client.post(
        "/auth/register", json={"email": "u@ex.com", "name": "U", "password": "longpassword"}
    )
    utok = reg.json()["access_token"]
    resp = await client.get("/admin/admins", headers=_auth(utok))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── §8.2 新增 / 列表 / 明細 ──


async def test_create_admin_201_and_not_protected(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    resp = await client.post(
        "/admin/admins",
        headers=_auth(token),
        json={"username": "newbie", "name": "N", "password": "longpassword"},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    body = resp.json()
    assert body["username"] == "newbie"
    assert body["admin_role"] == 0
    assert "is_protected" not in body  # AdminResponse 精簡、無狀態欄

    new_id = body["id"]
    detail = await client.get(f"/admin/admins/{new_id}", headers=_auth(token))
    assert detail.json()["is_protected"] is False


async def test_create_duplicate_username_409(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    payload = {"username": "dup", "name": "D", "password": "longpassword"}
    await client.post("/admin/admins", headers=_auth(token), json=payload)
    resp = await client.post("/admin/admins", headers=_auth(token), json=payload)
    assert resp.status_code == status.HTTP_409_CONFLICT


async def test_create_bad_username_400(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    resp = await client.post(
        "/admin/admins",
        headers=_auth(token),
        json={"username": "a@b", "name": "X", "password": "longpassword"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


async def test_create_short_password_422(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    resp = await client.post(
        "/admin/admins",
        headers=_auth(token),
        json={"username": "shortpw", "name": "X", "password": "short"},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_list_active_and_summary_shape(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    resp = await client.get("/admin/admins?status=active", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] >= 1
    item = body["items"][0]
    for key in ("is_protected", "is_active", "archived_at", "deleted_at", "created_at"):
        assert key in item


# ── §8.3 更新 / 升降權 / 密碼 ──


async def test_patch_name_200(client: AsyncClient, admin: Admin, db_session: AsyncSession) -> None:
    token = await _login(client)
    ed = await _mk(db_session, "renameme", AdminRole.VIEWER)
    resp = await client.patch(
        f"/admin/admins/{ed.id}", headers=_auth(token), json={"name": "Renamed"}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["name"] == "Renamed"


async def test_put_role_promote_200(
    client: AsyncClient, admin: Admin, db_session: AsyncSession
) -> None:
    token = await _login(client)
    ed = await _mk(db_session, "promo", AdminRole.EDITOR)
    resp = await client.put(
        f"/admin/admins/{ed.id}/role", headers=_auth(token), json={"admin_role": 100}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["admin_role"] == 100


async def test_put_role_other_admin_touch_protected_root_403(
    client: AsyncClient, admin: Admin, db_session: AsyncSession
) -> None:
    """§2.6：非本人的 admin 對受保護 root 動 role → 403（root 不可被他人修改）。"""
    token = await _login(client)
    root = await AdminService(db_session).create(
        username="proot",
        name="R",
        password="longpassword",
        admin_role=AdminRole.ROOT,
        is_protected=True,
    )
    resp = await client.put(
        f"/admin/admins/{root.id}/role", headers=_auth(token), json={"admin_role": 50}
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_change_own_password_204_then_old_refresh_dead(
    client: AsyncClient, admin: Admin
) -> None:
    login = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    token = login.json()["access_token"]
    old_refresh = login.json()["refresh_token"]

    resp = await client.post(
        "/admin/me/password",
        headers=_auth(token),
        json={"current_password": ADMIN_PASSWORD, "new_password": "brandnewpw1"},
    )
    assert resp.status_code == status.HTTP_204_NO_CONTENT
    # 舊 refresh 已撤
    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    # 新密碼可登入
    relog = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": "brandnewpw1"}
    )
    assert relog.status_code == status.HTTP_200_OK


async def test_change_own_password_wrong_old_401(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    resp = await client.post(
        "/admin/me/password",
        headers=_auth(token),
        json={"current_password": "wrongwrong", "new_password": "brandnewpw1"},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_change_own_password_same_400(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    resp = await client.post(
        "/admin/me/password",
        headers=_auth(token),
        json={"current_password": ADMIN_PASSWORD, "new_password": ADMIN_PASSWORD},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


async def test_no_reset_others_password_endpoint(
    client: AsyncClient, admin: Admin, db_session: AsyncSession
) -> None:
    token = await _login(client)
    ed = await _mk(db_session, "nopw", AdminRole.VIEWER)
    resp = await client.post(
        f"/admin/admins/{ed.id}/password", headers=_auth(token), json={"new_password": "x" * 10}
    )
    assert resp.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_405_METHOD_NOT_ALLOWED)


# ── §8.4 生命週期 + 守衛 ──


async def test_direct_archive_super_admin_422(
    client: AsyncClient, admin: Admin, db_session: AsyncSession
) -> None:
    token = await _login(client)
    sup = await _mk(db_session, "supx", AdminRole.SUPER_ADMIN)
    resp = await client.post(f"/admin/admins/{sup.id}/archive", headers=_auth(token))
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_self_delete_422(client: AsyncClient, admin: Admin) -> None:
    token = await _login(client)
    resp = await client.delete(f"/admin/admins/{admin.id}", headers=_auth(token))
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_two_step_remove_then_restore(
    client: AsyncClient, admin: Admin, db_session: AsyncSession
) -> None:
    token = await _login(client)
    sup = await _mk(db_session, "removeme", AdminRole.SUPER_ADMIN)
    # 先降級
    await client.put(f"/admin/admins/{sup.id}/role", headers=_auth(token), json={"admin_role": 0})
    # 再軟刪除 → 200 AdminSummary（deleted_at 有值）
    dele = await client.delete(f"/admin/admins/{sup.id}", headers=_auth(token))
    assert dele.status_code == status.HTTP_200_OK
    assert dele.json()["deleted_at"] is not None
    assert dele.json()["deleted_by_username"] == ADMIN_USERNAME  # L1 稽核者名稱

    # status=deleted 可見
    listed = await client.get("/admin/admins?status=deleted", headers=_auth(token))
    assert sup.id in {i["id"] for i in listed.json()["items"]}

    # restore → 200，可再登入
    restored = await client.post(f"/admin/admins/{sup.id}/restore", headers=_auth(token))
    assert restored.status_code == status.HTTP_200_OK
    relog = await client.post(
        "/admin/auth/login", json={"username": "removeme", "password": "longpassword"}
    )
    assert relog.status_code == status.HTTP_200_OK


async def test_archive_editor_then_login_401_and_idempotent(
    client: AsyncClient, admin: Admin, db_session: AsyncSession
) -> None:
    token = await _login(client)
    ed = await _mk(db_session, "arced", AdminRole.EDITOR)
    resp = await client.post(f"/admin/admins/{ed.id}/archive", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["archived_by_username"] == ADMIN_USERNAME
    # 被封存者不可登入
    relog = await client.post(
        "/admin/auth/login", json={"username": "arced", "password": "longpassword"}
    )
    assert relog.status_code == status.HTTP_401_UNAUTHORIZED
    # 再 archive → idempotent 200
    again = await client.post(f"/admin/admins/{ed.id}/archive", headers=_auth(token))
    assert again.status_code == status.HTTP_200_OK
    # unarchive → 200 可再登入
    un = await client.post(f"/admin/admins/{ed.id}/unarchive", headers=_auth(token))
    assert un.status_code == status.HTTP_200_OK
    relog2 = await client.post(
        "/admin/auth/login", json={"username": "arced", "password": "longpassword"}
    )
    assert relog2.status_code == status.HTTP_200_OK
