"""GET /admin/monitoring/db/history 整合測試（股價式折線圖 API）。"""

import json
import time

import redis.asyncio as redis
from fastapi import status
from httpx import AsyncClient

from app.core.config import get_app_settings
from app.models import Admin
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

REDIS_KEY = get_app_settings().monitoring_db_sorted_set_key


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin_token(client: AsyncClient) -> str:
    resp = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    return resp.json()["access_token"]


def _make_db_snapshot(ts: int) -> str:
    return json.dumps(
        {
            "ts": ts,
            "pool": {"size": 5, "checked_out": 1, "overflow": 0, "checked_in": 4},
            "connections": {"connected": 2, "running": 1, "idle": 1},
            "db_size_bytes": None,
            "longest_query_seconds": None,
            "backend": "mariadb",
        }
    )


async def test_db_history_no_auth_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/monitoring/db/history")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_db_history_empty_redis_returns_empty(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/db/history", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["snapshots"] == []


async def test_db_history_returns_snapshots_oldest_first(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """Redis 有 3 筆 → 200，snapshots 由舊到新（score 升冪）。"""
    now_ms = int(time.time() * 1000)
    t1, t2, t3 = now_ms - 10000, now_ms - 5000, now_ms
    for ts in [t2, t3, t1]:
        await fake_redis.zadd(REDIS_KEY, {_make_db_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/db/history", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 3
    assert snapshots[0]["ts"] == t1
    assert snapshots[1]["ts"] == t2
    assert snapshots[2]["ts"] == t3


async def test_db_history_range_filter_works(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    now_ms = int(time.time() * 1000)
    t1, t2, t3 = now_ms - 20000, now_ms - 10000, now_ms
    for ts in [t1, t2, t3]:
        await fake_redis.zadd(REDIS_KEY, {_make_db_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/db/history",
        params={"start_ms": t2, "end_ms": t3},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_200_OK
    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 2
    assert all(t2 <= s["ts"] <= t3 for s in snapshots)


async def test_db_history_only_start_ms(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    now_ms = int(time.time() * 1000)
    ts = now_ms - 1000
    await fake_redis.zadd(REDIS_KEY, {_make_db_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/db/history",
        params={"start_ms": ts - 100},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_200_OK
    assert len(resp.json()["snapshots"]) == 1


async def test_db_history_only_end_ms(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """僅帶 end_ms → 查詢範圍 [end_ms - default_ms, end_ms]。"""
    settings = get_app_settings()
    default_ms = settings.monitoring_infra_default_query_hours * 3_600_000
    now_ms = int(time.time() * 1000)
    end_ms = now_ms - 2000

    ts_in = end_ms - 1000
    ts_out = end_ms + 60_000
    ts_too_old = end_ms - default_ms - 60_000
    for ts in [ts_in, ts_out, ts_too_old]:
        await fake_redis.zadd(REDIS_KEY, {_make_db_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/db/history",
        params={"end_ms": end_ms},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_200_OK
    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["ts"] == ts_in


async def test_db_history_no_params_returns_default_range(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    now_ms = int(time.time() * 1000)
    ts = now_ms - 1000
    await fake_redis.zadd(REDIS_KEY, {_make_db_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/db/history", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert len(resp.json()["snapshots"]) == 1


async def test_db_history_start_gte_end_returns_400(client: AsyncClient, admin: Admin) -> None:
    now_ms = int(time.time() * 1000)
    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/db/history",
        params={"start_ms": now_ms, "end_ms": now_ms},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


async def test_db_history_range_exceeds_retention_returns_400(
    client: AsyncClient, admin: Admin
) -> None:
    settings = get_app_settings()
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (settings.monitoring_db_retention_hours + 1) * 3_600_000
    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/db/history",
        params={"start_ms": start_ms, "end_ms": now_ms},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
