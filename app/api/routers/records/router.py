"""Records CRUD + 匯入 + 分類下拉 API（records-api.md §3/§5）。

僅 admin（role=1）可用：讀 viewer+、寫 editor+（皆走 require_min_admin_role）。授權為安全底線，
不由前端 disable 取代（model §2.9）。業務規則/正規化在 service；本層轉呼叫 + DTO 解析。
路由順序：/records/categories 宣告在 /records/{id} 之前（避免靜態路徑被 int 攔截，§3）。
"""

from datetime import date as DateType

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import get_record_service, require_min_admin_role
from app.core.enums import AdminRole
from app.dtos import ImportResult, RecordCreate, RecordUpdate
from app.models import Admin
from app.services import RecordService

from .schemas import BulkCreateRequest, Category, RecordPage, RecordSummary

router: APIRouter = APIRouter(prefix="/records", tags=["records"])

_require_viewer = require_min_admin_role(AdminRole.VIEWER)
_require_editor = require_min_admin_role(AdminRole.EDITOR)


# ── 靜態路徑先於 /{id}（§3）──
@router.get("/categories", response_model=list[Category])
async def list_categories(
    _: Admin = Depends(_require_viewer),
    service: RecordService = Depends(get_record_service),
) -> list[Category]:
    cats = await service.list_categories()
    return [Category.from_model(c) for c in cats]


@router.get("", response_model=RecordPage)
async def list_records(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1),  # 不設 le；由 service 依情境夾值（§2.7-(1)）
    category: str | None = Query(None),
    keyword: str | None = Query(None),
    date_from: DateType | None = Query(
        None, description="篩選起始日（含，YYYY-MM-DD；UTC 00:00:00）"
    ),
    date_to: DateType | None = Query(
        None, description="篩選結束日（含當天末，YYYY-MM-DD；推進至隔日 UTC 00:00:00）"
    ),
    sort: str = Query("id:asc"),
    include_deleted: bool = Query(False),
    _: Admin = Depends(_require_viewer),
    service: RecordService = Depends(get_record_service),
) -> RecordPage:
    rows, total, page, size = await service.list_records(
        page=page,
        size=size,
        category=category,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        include_deleted=include_deleted,
    )
    return RecordPage(
        items=[RecordSummary.from_row(r) for r in rows], total=total, page=page, size=size
    )


@router.post("/bulk", response_model=ImportResult)
async def bulk_create(
    payload: BulkCreateRequest,
    actor: Admin = Depends(_require_editor),
    service: RecordService = Depends(get_record_service),
) -> ImportResult:
    """匯入：逐列驗證、非法進 errors、不中斷；200 + ImportResult（即使全失敗，§5.6）。"""
    return await service.bulk_create(payload.rows, actor)


@router.post("", response_model=RecordSummary, status_code=status.HTTP_201_CREATED)
async def create_record(
    payload: RecordCreate,
    actor: Admin = Depends(_require_editor),
    service: RecordService = Depends(get_record_service),
) -> RecordSummary:
    row = await service.create_record(payload, actor)
    return RecordSummary.from_row(row)


@router.get("/{record_id}", response_model=RecordSummary)
async def get_record(
    record_id: int,
    _: Admin = Depends(_require_viewer),
    service: RecordService = Depends(get_record_service),
) -> RecordSummary:
    row = await service.get_record(record_id)
    return RecordSummary.from_row(row)


@router.patch("/{record_id}", response_model=RecordSummary)
async def update_record(
    record_id: int,
    payload: RecordUpdate,
    actor: Admin = Depends(_require_editor),
    service: RecordService = Depends(get_record_service),
) -> RecordSummary:
    row = await service.update_record(record_id, payload, actor)
    return RecordSummary.from_row(row)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    record_id: int,
    actor: Admin = Depends(_require_editor),
    service: RecordService = Depends(get_record_service),
) -> Response:
    """軟刪除 → 204 無 body（對齊前端 delete_record -> None，§5.5）。"""
    await service.delete_record(record_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
