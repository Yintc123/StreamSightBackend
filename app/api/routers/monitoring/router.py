"""Monitoring API：日誌查詢 / DB 狀態 / 歷史指標（monitoring.md §2.7/§4）。

Infra 指標查詢：GET /monitoring/infra（infra-monitoring.md §5）。
"""

import json
import time

from fastapi import APIRouter, Depends, Query
from redis.exceptions import RedisError

from app.api.dependencies import get_current_admin, get_redis, require_min_admin_role
from app.api.dependencies.services import (
    get_db_stats_service,
    get_log_query_service,
    get_metric_query_service,
)
from app.core.config import get_app_settings
from app.core.enums import AdminRole
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.dtos.dto_monitoring import (
    DbHistoryResponse,
    DbSample,
    InfraHistoryResponse,
    InfraSnapshot,
    LogEntry,
    Page,
)
from app.models import Admin
from app.services.monitoring.db_stats import DbStatsService
from app.services.monitoring.logs import LogQueryService
from app.services.monitoring.metrics import MetricQueryService

router: APIRouter = APIRouter(prefix="/monitoring", tags=["monitoring"])

# 日誌：敏感，限 SUPER_ADMIN（monitoring.md §2.6/§6）
_require_log_role = require_min_admin_role(AdminRole.SUPER_ADMIN)
# DB 狀態 / 指標：任一 admin 可看
_require_viewer = require_min_admin_role(AdminRole.VIEWER)


@router.get("/logs", response_model=Page[LogEntry])
async def get_logs(
    level: str | None = Query(default=None, description="篩選 log level"),
    since: int | None = Query(default=None, description="起始時刻（epoch ms）"),
    until: int | None = Query(default=None, description="結束時刻（epoch ms）"),
    request_id: str | None = Query(default=None, description="篩選 request_id"),
    logger: str | None = Query(default=None, description="篩選 logger 名稱"),
    cursor: str | None = Query(default=None, description="游標（上頁 next_cursor）"),
    limit: int = Query(default=100, ge=1, description="每頁上限"),
    _admin: Admin = Depends(get_current_admin),
    _role: None = Depends(_require_log_role),
    svc: LogQueryService = Depends(get_log_query_service),
) -> Page[LogEntry]:
    settings = get_app_settings()
    clamped_limit = min(limit, settings.monitoring_query_max_limit)
    return await svc.query(
        level=level,
        since=since,
        until=until,
        request_id=request_id,
        logger=logger,
        cursor=cursor,
        limit=clamped_limit,
    )


@router.get("/db", response_model=DbSample)
async def get_db_snapshot(
    _admin: Admin = Depends(get_current_admin),
    _role: None = Depends(_require_viewer),
    svc: DbStatsService = Depends(get_db_stats_service),
) -> DbSample:
    return await svc.snapshot()


@router.get("/metrics/{name}", response_model=Page[dict])
async def get_metrics(
    name: str,
    since: int | None = Query(default=None, description="起始時刻（epoch ms）"),
    until: int | None = Query(default=None, description="結束時刻（epoch ms）"),
    cursor: str | None = Query(default=None, description="游標"),
    limit: int = Query(default=100, ge=1, description="每頁上限"),
    _admin: Admin = Depends(get_current_admin),
    _role: None = Depends(_require_viewer),
    svc: MetricQueryService = Depends(get_metric_query_service),
) -> Page[dict]:
    settings = get_app_settings()
    clamped_limit = min(limit, settings.monitoring_query_max_limit)
    return await svc.range(name, since=since, until=until, cursor=cursor, limit=clamped_limit)


@router.get("/db/history", response_model=DbHistoryResponse)
async def get_db_history(
    start_ms: int | None = Query(None, ge=0, description="查詢起始時間（epoch ms，含）"),
    end_ms: int | None = Query(None, ge=0, description="查詢結束時間（epoch ms，含）"),
    _admin: Admin = Depends(get_current_admin),
    redis=Depends(get_redis),
) -> DbHistoryResponse:
    """DB 狀態指標歷史查詢，股價式折線圖（ZADD Sorted Set，對齊 /infra 語意）。"""
    settings = get_app_settings()
    now_ms = int(time.time() * 1000)
    default_ms = settings.monitoring_infra_default_query_hours * 3_600_000
    retention_ms = settings.monitoring_db_retention_hours * 3_600_000

    resolved_end = end_ms if end_ms is not None else now_ms
    resolved_start = start_ms if start_ms is not None else (resolved_end - default_ms)

    if resolved_start >= resolved_end:
        raise BadRequestError("start_ms must be less than end_ms")
    if resolved_end - resolved_start > retention_ms:
        raise BadRequestError(
            f"Query range exceeds retention window ({settings.monitoring_db_retention_hours}h)"
        )

    try:
        raw_list = await redis.zrangebyscore(
            settings.monitoring_db_sorted_set_key, resolved_start, resolved_end
        )
    except RedisError as exc:
        raise ServiceUnavailableError("Redis unavailable") from exc

    snapshots = [DbSample(**json.loads(item)) for item in raw_list]
    return DbHistoryResponse(snapshots=snapshots)


@router.get("/infra", response_model=InfraHistoryResponse)
async def get_infra(
    start_ms: int | None = Query(None, ge=0, description="查詢起始時間（epoch ms，含）"),
    end_ms: int | None = Query(None, ge=0, description="查詢結束時間（epoch ms，含）"),
    _admin: Admin = Depends(get_current_admin),
    redis=Depends(get_redis),
) -> InfraHistoryResponse:
    """OS / DB 基礎設施指標歷史查詢（infra-monitoring.md §5）。"""
    settings = get_app_settings()
    now_ms = int(time.time() * 1000)
    default_ms = settings.monitoring_infra_default_query_hours * 3_600_000
    retention_ms = settings.monitoring_infra_retention_hours * 3_600_000

    resolved_end = end_ms if end_ms is not None else now_ms
    resolved_start = start_ms if start_ms is not None else (resolved_end - default_ms)

    if resolved_start >= resolved_end:
        raise BadRequestError("start_ms must be less than end_ms")
    if resolved_end - resolved_start > retention_ms:
        raise BadRequestError(
            f"Query range exceeds retention window ({settings.monitoring_infra_retention_hours}h)"
        )

    try:
        raw_list = await redis.zrangebyscore(
            settings.monitoring_infra_redis_key, resolved_start, resolved_end
        )
    except RedisError as exc:
        raise ServiceUnavailableError("Redis unavailable") from exc

    snapshots = [InfraSnapshot(**json.loads(item)) for item in raw_list]
    return InfraHistoryResponse(snapshots=snapshots)
