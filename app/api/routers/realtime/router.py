"""Realtime history API（realtime-history.md §5.3）。

GET /realtime/history — 以時間範圍查詢歷史讀值；授權等級 AdminRole.VIEWER。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_realtime_history_service, require_min_admin_role
from app.core.enums import AdminRole
from app.models import Admin
from app.services.realtime.history import RealtimeHistoryService

from .schemas import HistoryPage

router: APIRouter = APIRouter(prefix="/realtime", tags=["realtime"])

_require_viewer = require_min_admin_role(AdminRole.VIEWER)


@router.get("/history", response_model=HistoryPage)
async def list_history(
    from_: datetime = Query(..., alias="from", description="查詢起始時間（含，UTC ISO 8601）"),
    to: datetime = Query(..., description="查詢結束時間（不含，UTC ISO 8601）"),
    size: int = Query(1000, ge=1, le=5000, description="回傳筆數上限（1–5000）"),
    _: Admin = Depends(_require_viewer),
    service: RealtimeHistoryService = Depends(get_realtime_history_service),
) -> HistoryPage:
    items = await service.list_history(from_, to, size)
    return HistoryPage.from_query(items, from_, to)
