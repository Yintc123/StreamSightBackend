"""GET /monitoring/db 與 /metrics/{name} 整合測試（monitoring.md §7.4/§7.7）。"""

import redis.asyncio as redis
from fastapi import status
from httpx import AsyncClient

from app.models import Admin
from app.services.monitoring.store import RedisStreamStore
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin_token(client: AsyncClient) -> str:
    resp = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    return resp.json()["access_token"]


async def test_db_no_auth_401(client: AsyncClient) -> None:
    resp = await client.get("/monitoring/db")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_db_returns_snapshot(client: AsyncClient, admin: Admin) -> None:
    """SQLite 測試環境走 PoolStatsProbe（任何後端皆可），驗回應結構（monitoring.md §7.4）。"""
    token = await _admin_token(client)
    resp = await client.get("/monitoring/db", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "pool" in data
    assert "connections" in data
    assert "backend" in data


async def test_metrics_db_range(client: AsyncClient, admin: Admin, fake_redis: redis.Redis) -> None:
    """先寫樣本進 stream，再查歷史（monitoring.md §7.7）。"""
    store = RedisStreamStore(fake_redis)
    await store.append(
        "monitor:stream:db",
        {"ts": "1000", "pool": "{}", "connections": "{}", "backend": "pool_only"},
        maxlen=1000,
    )
    token = await _admin_token(client)
    resp = await client.get("/monitoring/metrics/db", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) == 1


async def test_metrics_unknown_name_empty(client: AsyncClient, admin: Admin) -> None:
    """未知 metric name → 空 Page（monitoring.md §2.7）。"""
    token = await _admin_token(client)
    resp = await client.get("/monitoring/metrics/unknown_xyz", headers=_auth(token))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["items"] == []
