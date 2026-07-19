"""Integration tests for GET /realtime/history（realtime-history.md §6.3）。

TDD: RED → GREEN → REFACTOR。
使用 client fixture（ASGITransport，不跑 lifespan；DB = SQLite in-memory）。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.repositories.repo_realtime_reading import RealtimeReadingRepository
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def _ts(offset_seconds: int = 0) -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=offset_seconds)


async def _login(client, username=ADMIN_USERNAME, password=ADMIN_PASSWORD) -> str:
    resp = await client.post("/admin/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


# ── test 11：無 token → 401 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_no_token(client) -> None:
    """無 token → 401（§6.3 test 11）。"""
    resp = await client.get(
        "/realtime/history",
        params={"from": "2026-07-19T00:00:00Z", "to": "2026-07-19T01:00:00Z"},
    )
    assert resp.status_code == 401


# ── test 12：viewer 可查詢 → 200 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_viewer_ok(client, admin) -> None:
    """viewer 角色可查詢 → 200（§6.3 test 12）。"""
    token = await _login(client)
    resp = await client.get(
        "/realtime/history",
        params={"from": "2026-07-19T00:00:00Z", "to": "2026-07-19T01:00:00Z"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


# ── test 13：時間範圍過濾正確 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_range_filter(client, admin, db_session) -> None:
    """時間範圍過濾：插入 3 筆不同時間 → from/to 只回傳符合區間（§6.3 test 13）。"""
    base = _ts(0)
    repo = RealtimeReadingRepository(db_session)
    rows = [{"value": float(i), "ts": base + timedelta(minutes=i * 10)} for i in range(3)]
    await repo.bulk_insert(rows)
    await db_session.flush()

    token = await _login(client)
    # 只涵蓋第 0 和第 1 筆（base ~ base+15min）
    from_str = (base - timedelta(seconds=1)).isoformat() + "Z"
    to_str = (base + timedelta(minutes=15)).isoformat() + "Z"
    resp = await client.get(
        "/realtime/history",
        params={"from": from_str, "to": to_str},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2


# ── test 14：size 超上限 5000 → 422 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_size_too_large(client, admin) -> None:
    """size=9999 超出上限 5000 → 422（§6.3 test 14）。"""
    token = await _login(client)
    resp = await client.get(
        "/realtime/history",
        params={"from": "2026-07-19T00:00:00Z", "to": "2026-07-19T01:00:00Z", "size": 9999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# ── test 15：回應結構正確 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_response_structure(client, admin, db_session) -> None:
    """回應結構：items[].value（float）/ items[].ts（ISO8601 UTC）/ from / to（§6.3 test 15）。"""
    base = _ts(0)
    repo = RealtimeReadingRepository(db_session)
    await repo.bulk_insert([{"value": 42.3, "ts": base}])
    await db_session.flush()

    token = await _login(client)
    from_str = (base - timedelta(seconds=1)).isoformat() + "Z"
    to_str = (base + timedelta(seconds=10)).isoformat() + "Z"
    resp = await client.get(
        "/realtime/history",
        params={"from": from_str, "to": to_str},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # 頂層欄位
    assert "items" in data
    assert "from" in data
    assert "to" in data

    # items 結構
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert isinstance(item["value"], float)
    assert "ts" in item
    ts_str: str = item["ts"]
    # ts 必須帶 UTC 時區後綴（+00:00 或 Z）——規格書 §5.3 response schema
    assert isinstance(ts_str, str)
    assert ts_str.endswith("+00:00") or ts_str.endswith("Z"), (
        f"ts 必須含 UTC 後綴，實際得到 {ts_str!r}"
    )
