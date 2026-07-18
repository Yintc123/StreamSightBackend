"""GET /admin/monitoring/infra 整合測試（infra-monitoring.md §6.4）。"""

import json
import time

import fakeredis.aioredis
import redis.asyncio as redis
from fastapi import status
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport

from app.api.dependencies import get_redis, get_session
from app.app import create_app
from app.core.config import get_app_settings
from app.models import Admin
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

REDIS_KEY = get_app_settings().monitoring_infra_redis_key


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin_token(client: AsyncClient) -> str:
    resp = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    return resp.json()["access_token"]


def _make_snapshot(ts: int) -> str:
    return json.dumps(
        {
            "ts": ts,
            "cpu_percent": 25.0,
            "memory_percent": 60.0,
            "disk_percent": 45.0,
            "disk_read_iops": 10.0,
            "disk_write_iops": 5.0,
            "db_connections": 3,
            "db_buffer_pool_hit_rate": 99.0,
        }
    )


async def test_infra_no_auth_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/monitoring/infra")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_infra_empty_redis_returns_empty(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """Redis 為空 → 200 + {"snapshots": []}。"""
    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/infra", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["snapshots"] == []


async def test_infra_returns_snapshots_oldest_first(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """Redis 有 3 筆 → 200，snapshots 由小到大（舊到新）。"""
    now_ms = int(time.time() * 1000)
    t1, t2, t3 = now_ms - 10000, now_ms - 5000, now_ms
    for ts in [t2, t3, t1]:  # 刻意亂序寫入
        await fake_redis.zadd(REDIS_KEY, {_make_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/infra", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 3
    assert snapshots[0]["ts"] == t1
    assert snapshots[1]["ts"] == t2
    assert snapshots[2]["ts"] == t3


async def test_infra_range_filter_works(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """帶 start_ms / end_ms → 只回傳範圍內的筆。"""
    now_ms = int(time.time() * 1000)
    t1 = now_ms - 20000
    t2 = now_ms - 10000
    t3 = now_ms
    for ts in [t1, t2, t3]:
        await fake_redis.zadd(REDIS_KEY, {_make_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/infra",
        params={"start_ms": t2, "end_ms": t3},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_200_OK
    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 2
    assert all(t2 <= s["ts"] <= t3 for s in snapshots)


async def test_infra_only_start_ms(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """僅帶 start_ms，不帶 end_ms → 不報錯，回傳 start_ms 至今的資料。"""
    now_ms = int(time.time() * 1000)
    ts = now_ms - 1000
    await fake_redis.zadd(REDIS_KEY, {_make_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/infra",
        params={"start_ms": ts - 100},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_200_OK
    assert len(resp.json()["snapshots"]) == 1


async def test_infra_only_end_ms(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """僅帶 end_ms，不帶 start_ms → 查詢範圍為 [end_ms - default_ms, end_ms]（§2.4 第 3 情境）。"""
    settings = get_app_settings()
    default_ms = settings.monitoring_infra_default_query_hours * 3_600_000
    now_ms = int(time.time() * 1000)
    end_ms = now_ms - 2000  # 查詢截止點設在 2 秒前

    ts_in = end_ms - 1000  # 在查詢窗內（end_ms 前 1 秒）
    ts_out = end_ms + 60_000  # 超出 end_ms → 不應回傳
    ts_too_old = end_ms - default_ms - 60_000  # 超出 [end_ms - default_ms] 下界 → 不應回傳
    for ts in [ts_in, ts_out, ts_too_old]:
        await fake_redis.zadd(REDIS_KEY, {_make_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/infra",
        params={"end_ms": end_ms},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_200_OK
    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["ts"] == ts_in


async def test_infra_no_params_returns_default_range(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """兩者皆不帶 → 不報錯，預設查最近 1 小時。"""
    now_ms = int(time.time() * 1000)
    ts = now_ms - 1000
    await fake_redis.zadd(REDIS_KEY, {_make_snapshot(ts): ts})

    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/infra", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert len(resp.json()["snapshots"]) == 1


async def test_infra_start_gte_end_returns_400(client: AsyncClient, admin: Admin) -> None:
    """start_ms >= end_ms → 400。"""
    now_ms = int(time.time() * 1000)
    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/infra",
        params={"start_ms": now_ms, "end_ms": now_ms},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


async def test_infra_range_exceeds_retention_returns_400(client: AsyncClient, admin: Admin) -> None:
    """end_ms - start_ms > retention_hours * 3_600_000 → 400。"""
    settings = get_app_settings()
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (settings.monitoring_infra_retention_hours + 1) * 3_600_000
    token = await _admin_token(client)
    resp = await client.get(
        "/admin/monitoring/infra",
        params={"start_ms": start_ms, "end_ms": now_ms},
        headers=_auth(token),
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


async def test_infra_redis_unavailable_returns_503(db_session, admin: Admin) -> None:
    """Redis 不可用（zrangebyscore 拋 RedisError）→ 503。"""
    from redis.exceptions import RedisError

    class BrokenRedis:
        async def zrangebyscore(self, *args, **kwargs):
            raise RedisError("redis down")

    app = create_app()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_redis] = BrokenRedis

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as broken_client:
        # 先用正常 client 取 token
        normal_app = create_app()
        normal_fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        normal_app.dependency_overrides[get_session] = override_get_session
        normal_app.dependency_overrides[get_redis] = lambda: normal_fake
        async with AsyncClient(
            transport=ASGITransport(app=normal_app), base_url="http://test"
        ) as nc:
            token = await _admin_token(nc)

        resp = await broken_client.get("/admin/monitoring/infra", headers=_auth(token))
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
