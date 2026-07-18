from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


def _mock_httpx_ok() -> MagicMock:
    """回傳模擬 200 回應的 AsyncClient context manager mock。"""
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_resp
    return mock_client


def _mock_httpx_err() -> MagicMock:
    """回傳模擬 ConnectError 的 AsyncClient context manager mock。"""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.side_effect = httpx.ConnectError("Connection refused")
    return mock_client


_PATCH_TARGET = "app.api.routers.health.router.httpx.AsyncClient"


async def test_health_node_exporter_ok(client: AsyncClient) -> None:
    """node-exporter 可達 → 200 + status=ok + response_time_ms 非負。"""
    with patch(_PATCH_TARGET, return_value=_mock_httpx_ok()):
        response: Response = await client.get("/health/node-exporter")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"
    assert data["response_time_ms"] >= 0
    assert data["error"] is None


async def test_health_node_exporter_unreachable(client: AsyncClient) -> None:
    """node-exporter 不可達 → 200 + status=unreachable + error 非空。"""
    with patch(_PATCH_TARGET, return_value=_mock_httpx_err()):
        response: Response = await client.get("/health/node-exporter")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "unreachable"
    assert data["error"] is not None
    assert data["response_time_ms"] is None


async def test_health_mysqld_exporter_ok(client: AsyncClient) -> None:
    """mysqld-exporter 可達 → 200 + status=ok。"""
    with patch(_PATCH_TARGET, return_value=_mock_httpx_ok()):
        response: Response = await client.get("/health/mysqld-exporter")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"
    assert data["response_time_ms"] >= 0


async def test_health_mysqld_exporter_unreachable(client: AsyncClient) -> None:
    """mysqld-exporter 不可達 → 200 + status=unreachable。"""
    with patch(_PATCH_TARGET, return_value=_mock_httpx_err()):
        response: Response = await client.get("/health/mysqld-exporter")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "unreachable"
    assert data["error"] is not None
