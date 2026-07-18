"""GET /admin/monitoring/logs 整合測試（monitoring.md §7.3）。"""

import redis.asyncio as redis
from fastapi import status
from httpx import AsyncClient

from app.models import Admin
from app.services.monitoring.store import RedisStreamStore
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

STREAM = "monitor:stream:logs"


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin_token(client: AsyncClient) -> str:
    resp = await client.post(
        "/admin/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    return resp.json()["access_token"]


async def _seed_logs(fake_redis: redis.Redis, entries: list[dict]) -> None:
    store = RedisStreamStore(fake_redis)
    for entry in entries:
        await store.append(STREAM, entry, maxlen=10000)


async def test_logs_no_auth_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/monitoring/logs")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_logs_empty_returns_200(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/logs", headers=_auth_header(token))
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["items"] == []
    assert data["next_cursor"] is None


async def test_logs_returns_entries(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    await _seed_logs(
        fake_redis,
        [
            {"ts": "1000", "level": "INFO", "logger": "app.auth", "message": "login ok"},
            {"ts": "2000", "level": "ERROR", "logger": "app.ws", "message": "ws error"},
        ],
    )
    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/logs", headers=_auth_header(token))
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data["items"]) == 2


async def test_logs_filter_by_level(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    await _seed_logs(
        fake_redis,
        [
            {"ts": "1000", "level": "INFO", "logger": "x", "message": "ok"},
            {"ts": "2000", "level": "ERROR", "logger": "x", "message": "bad"},
        ],
    )
    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/logs?level=ERROR", headers=_auth_header(token))
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["level"] == "ERROR"


async def test_logs_limit_and_cursor_pagination(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    await _seed_logs(
        fake_redis,
        [{"ts": str(i), "level": "INFO", "logger": "x", "message": f"m{i}"} for i in range(6)],
    )
    token = await _admin_token(client)
    resp1 = await client.get("/admin/monitoring/logs?limit=4", headers=_auth_header(token))
    assert resp1.status_code == status.HTTP_200_OK
    data1 = resp1.json()
    assert len(data1["items"]) == 4
    assert data1["next_cursor"] is not None

    resp2 = await client.get(
        f"/admin/monitoring/logs?limit=4&cursor={data1['next_cursor']}",
        headers=_auth_header(token),
    )
    data2 = resp2.json()
    assert len(data2["items"]) == 2
    assert data2["next_cursor"] is None


async def test_logs_limit_clamped_to_max(
    client: AsyncClient, admin: Admin, fake_redis: redis.Redis
) -> None:
    """limit 超出設定上限自動 clamp（monitoring.md §2.7）。"""
    token = await _admin_token(client)
    resp = await client.get("/admin/monitoring/logs?limit=99999", headers=_auth_header(token))
    assert resp.status_code == status.HTTP_200_OK
