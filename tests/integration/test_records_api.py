"""Integration tests for Records API（/records*）——records-api.md §8。

沿用 `admin` fixture（super_admin＝可寫）與 `record_categories`（四分類）。viewer/editor 以
AdminService.create 佈局、_login 取 token、_auth 帶 header（比照 test_admin_management_api）。
"""

from datetime import date

from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AdminRole
from app.models import Admin
from app.models.record_category import RecordCategory
from app.services import AdminService
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


async def _login(
    client: AsyncClient, username: str = ADMIN_USERNAME, pw: str = ADMIN_PASSWORD
) -> str:
    resp = await client.post("/admin/auth/login", json={"username": username, "password": pw})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _mk_admin(db: AsyncSession, username: str, role: AdminRole) -> Admin:
    return await AdminService(db).create(
        username=username, name=username, password="longpassword", admin_role=role
    )


async def _create(client: AsyncClient, token: str, **over) -> dict:
    body = {"title": "T", "value": 1.0, "category": "感測器", "note": ""}
    body.update(over)
    resp = await client.post("/records", headers=_auth(token), json=body)
    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    return resp.json()


# ── §8.1 授權 ──────────────────────────────────────────────────
async def test_viewer_can_read_but_not_write(
    client: AsyncClient, db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    await _mk_admin(db_session, "rec-viewer", AdminRole.VIEWER)
    token = await _login(client, "rec-viewer", "longpassword")
    assert (await client.get("/records", headers=_auth(token))).status_code == status.HTTP_200_OK
    write = await client.post(
        "/records", headers=_auth(token), json={"title": "X", "value": 1.0, "category": "感測器"}
    )
    assert write.status_code == status.HTTP_403_FORBIDDEN


async def test_no_token_401(client: AsyncClient, record_categories: list[RecordCategory]) -> None:
    assert (await client.get("/records")).status_code == status.HTTP_401_UNAUTHORIZED


async def test_editor_can_write(
    client: AsyncClient, db_session: AsyncSession, record_categories: list[RecordCategory]
) -> None:
    await _mk_admin(db_session, "rec-editor", AdminRole.EDITOR)
    token = await _login(client, "rec-editor", "longpassword")
    created = await _create(client, token, title="E")
    assert created["created_by"] == "rec-editor"


# ── §8.2 列表 ──────────────────────────────────────────────────
async def test_list_pagination_and_total(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    for i in range(3):
        await _create(client, token, title=f"r{i}")
    resp = await client.get("/records?page=1&size=2", headers=_auth(token))
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["page"] == 1 and body["size"] == 2


async def test_list_size_clamped_not_422(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    resp = await client.get("/records?size=1000000", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["size"] == 100  # service 夾值，非 422


async def test_list_keyword_and_category_filter(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    await _create(client, token, title="Alpha", category="感測器")
    await _create(client, token, title="Beta", category="系統")
    kw = await client.get("/records?keyword=alph", headers=_auth(token))
    assert [i["title"] for i in kw.json()["items"]] == ["Alpha"]
    cat = await client.get("/records?category=系統", headers=_auth(token))
    assert [i["title"] for i in cat.json()["items"]] == ["Beta"]


async def test_list_invalid_sort_422(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    resp = await client.get("/records?sort=bogus:asc", headers=_auth(token))
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_list_include_deleted(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    rec = await _create(client, token, title="willdelete")
    await client.delete(f"/records/{rec['id']}", headers=_auth(token))
    default = await client.get("/records", headers=_auth(token))
    assert "willdelete" not in [i["title"] for i in default.json()["items"]]
    incl = await client.get("/records?include_deleted=true", headers=_auth(token))
    assert "willdelete" in [i["title"] for i in incl.json()["items"]]


# ── §8.3 單筆 / 建立 / 更新 / 刪除 ───────────────────────────────
async def test_get_hit_and_miss(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    rec = await _create(client, token, title="one")
    hit = await client.get(f"/records/{rec['id']}", headers=_auth(token))
    assert hit.status_code == status.HTTP_200_OK
    assert hit.json()["title"] == "one"
    miss = await client.get("/records/99999", headers=_auth(token))
    assert miss.status_code == status.HTTP_404_NOT_FOUND


async def test_create_unknown_category_422(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    resp = await client.post(
        "/records", headers=_auth(token), json={"title": "X", "value": 1.0, "category": "不存在"}
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_patch_updates_fields(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    rec = await _create(client, token, title="Orig", value=1.0)
    resp = await client.patch(
        f"/records/{rec['id']}",
        headers=_auth(token),
        json={"title": "New", "value": 9.0, "category": "系統", "note": "n"},
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["title"] == "New" and body["value"] == 9.0 and body["category"] == "系統"
    assert body["created_by"] == ADMIN_USERNAME  # 建立者不變


async def test_delete_204_then_get_404(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    rec = await _create(client, token, title="del")
    d = await client.delete(f"/records/{rec['id']}", headers=_auth(token))
    assert d.status_code == status.HTTP_204_NO_CONTENT
    assert d.content == b""
    g = await client.get(f"/records/{rec['id']}", headers=_auth(token))
    assert g.status_code == status.HTTP_404_NOT_FOUND


# ── §8.4 匯入 ──────────────────────────────────────────────────
async def test_bulk_mixed_returns_200_with_errors(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    rows = [
        {"title": "ok", "value": 1.0, "category": "感測器"},
        {"title": "", "value": 1.0, "category": "感測器"},
        {"title": "bad", "value": 1.0, "category": "不存在"},
    ]
    resp = await client.post("/records/bulk", headers=_auth(token), json={"rows": rows})
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["created"] == 1
    assert [e["row_index"] for e in body["errors"]] == [1, 2]


async def test_bulk_over_1000_422(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    rows = [{"title": "x", "value": 1.0, "category": "感測器"}] * 1001
    resp = await client.post("/records/bulk", headers=_auth(token), json={"rows": rows})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ── §8.5 分類 & 回應形狀 ─────────────────────────────────────────
async def test_categories_active_sorted(
    client: AsyncClient,
    admin: Admin,
    db_session: AsyncSession,
    record_categories: list[RecordCategory],
) -> None:
    db_session.add(RecordCategory(name="退場", label="退場", is_active=False, sort_order=99))
    await db_session.flush()
    token = await _login(client)
    resp = await client.get("/records/categories", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    names = [c["name"] for c in resp.json()]
    assert names == ["感測器", "系統", "應用", "網路"]
    assert set(resp.json()[0].keys()) == {"name", "label", "sort_order"}


async def test_record_summary_shape_no_updated_by(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    token = await _login(client)
    rec = await _create(client, token, title="shape")
    assert set(rec.keys()) == {
        "id",
        "title",
        "value",
        "category",
        "created_by",
        "created_at",
        "updated_at",
        "note",
        "deleted_at",
    }
    assert "updated_by" not in rec


# ── §8.6 日期範圍篩選（§2.7-(2)）────────────────────────────────


async def test_list_date_from_future_returns_empty(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    """date_from 設遠未來 → total=0（端點正確傳遞至 service → repo）。"""
    token = await _login(client)
    await _create(client, token, title="now")
    resp = await client.get("/records?date_from=2999-12-31", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["total"] == 0


async def test_list_date_to_past_returns_empty(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    """date_to 設遠過去 → total=0。"""
    token = await _login(client)
    await _create(client, token, title="now")
    resp = await client.get("/records?date_to=2000-01-01", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["total"] == 0


async def test_list_date_range_today_includes_record(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    """date_from=today&date_to=today 含當天建立的 record（date_to 推進至明日，開區間）。"""
    token = await _login(client)
    created = await _create(client, token, title="today-record")
    today = date.today().isoformat()
    resp = await client.get(f"/records?date_from={today}&date_to={today}", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["id"] == created["id"] for item in body["items"])


async def test_list_date_range_analytics_size_not_clamped_to_100(
    client: AsyncClient, admin: Admin, record_categories: list[RecordCategory]
) -> None:
    """有日期範圍時 size=1000 不被夾至 100（使用 analytics_max=5000）。"""
    token = await _login(client)
    today = date.today().isoformat()
    resp = await client.get(f"/records?date_from={today}&size=1000", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["size"] == 1000  # analytics context，不夾至 100
