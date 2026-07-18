"""Admin 監控 API：日誌查詢 / DB 狀態 / 歷史指標（monitoring.md §2.7/§4）。"""

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_admin, require_min_admin_role
from app.api.dependencies.services import (
    get_db_stats_service,
    get_log_query_service,
    get_metric_query_service,
)
from app.core.config import get_app_settings
from app.core.enums import AdminRole
from app.dtos.monitoring import DbSample, LogEntry, Page
from app.models import Admin
from app.services.monitoring.db_stats import DbStatsService
from app.services.monitoring.logs import LogQueryService
from app.services.monitoring.metrics import MetricQueryService

router: APIRouter = APIRouter(prefix="/admin/monitoring", tags=["monitoring"])

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
