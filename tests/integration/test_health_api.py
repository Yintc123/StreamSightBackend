from typing import Any

from fastapi import status
from httpx import AsyncClient, Response


async def test_health_returns_ok(client: AsyncClient) -> None:
    response: Response = await client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    data: dict[str, Any] = response.json()
    assert data["message"] == "ok"
    assert "app_version" in data


async def test_health_db_returns_ok(client: AsyncClient) -> None:
    response: Response = await client.get("/health/db")

    assert response.status_code == status.HTTP_200_OK
    data: dict[str, Any] = response.json()
    assert data["db"] == "ok"
    assert data["result"] == 1


async def test_health_redis_returns_ok(client: AsyncClient) -> None:
    """靠 dependency override 和 endpoint 拿到 fake_redis。"""
    response: Response = await client.get("/health/redis")

    assert response.status_code == status.HTTP_200_OK
    data: dict[str, Any] = response.json()
    assert data["redis"] == "ok"
    assert data["ping"] is True
