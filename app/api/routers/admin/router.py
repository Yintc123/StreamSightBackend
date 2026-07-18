"""Admin 認證 + 管理 API。

- 認證（既有）：POST /admin/auth/login、GET /admin/me。
- 自助：POST /admin/me/password（改自己密碼）。
- 管理他人（限 SUPER_ADMIN）：/admin/admins/... CRUD + 生命週期 + 升降權。

業務不變式由 service 強制（受保護 root、super_admin 須先降級、禁對自己／自我提權）；
本層僅轉呼叫、傳 actor_principal_id 供稽核，例外經全域 handler 映射狀態碼。見 api §2/§5/§6。
"""

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import (
    get_admin_service,
    get_auth_service,
    get_current_admin,
    require_min_admin_role,
)
from app.api.routers.auth.schemas import TokenResponse
from app.core.config import get_app_settings
from app.core.enums import AdminRole, AdminStatusFilter
from app.dtos import AdminLoginRequest, TokenPayload
from app.models import Admin
from app.services import AdminService, AuthService

from .schemas import (
    AdminCreateRequest,
    AdminListResponse,
    AdminResponse,
    AdminRoleUpdateRequest,
    AdminSummary,
    AdminUpdateRequest,
    ChangeOwnPasswordRequest,
)

router: APIRouter = APIRouter(prefix="/admin", tags=["admin"])

# 管理他人 admin：一律限 SUPER_ADMIN（api §2）。回傳當前 admin 供取 actor_principal_id。
_require_super = require_min_admin_role(AdminRole.SUPER_ADMIN)


def _to_token_response(token: TokenPayload) -> TokenResponse:
    """Wrap a service TokenPayload into the API TokenResponse, filling expires_in."""
    return TokenResponse(
        **token.model_dump(),
        expires_in=get_app_settings().jwt_access_token_expire_seconds,
    )


async def _summary(service: AdminService, admin_id: int) -> AdminSummary:
    """明細／生命週期回身：帶稽核者 username 的 AdminSummary（含軟刪除者）。"""
    row = await service.get_row(admin_id, include_deleted=True)
    return AdminSummary.from_row(row)


# ── 認證（既有）──


@router.post("/auth/login", response_model=TokenResponse)
async def admin_login(
    payload: AdminLoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Verify admin credentials and return a role=1 access + refresh token."""
    token: TokenPayload = await service.admin_login(payload)
    return _to_token_response(token)


@router.get("/me", response_model=AdminResponse)
async def admin_me(current_admin: Admin = Depends(get_current_admin)) -> Admin:
    """Return the current authenticated admin (protected by get_current_admin)."""
    return current_admin


# ── 自助改密碼 ──


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(
    payload: ChangeOwnPasswordRequest,
    current_admin: Admin = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
) -> Response:
    """改自己密碼（需舊）：成功後全部 refresh token 已撤，需重新登入。api §5。"""
    await service.change_password(
        current_admin.id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── 管理他人 admin（限 SUPER_ADMIN）──


@router.get("/admins", response_model=AdminListResponse)
async def list_admins(
    status_filter: AdminStatusFilter = Query(AdminStatusFilter.ACTIVE, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> AdminListResponse:
    rows, total = await service.list_admins(status=status_filter, limit=limit, offset=offset)
    return AdminListResponse(
        items=[AdminSummary.from_row(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/admins", response_model=AdminResponse, status_code=status.HTTP_201_CREATED)
async def create_admin(
    payload: AdminCreateRequest,
    _: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> Admin:
    """新增一般 admin（恆 is_protected=False，不開放經 API 建受保護 admin）。"""
    return await service.create(
        username=payload.username,
        name=payload.name,
        password=payload.password,
        admin_role=payload.admin_role,
    )


@router.get("/admins/{admin_id}", response_model=AdminSummary)
async def get_admin(
    admin_id: int,
    _: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> AdminSummary:
    return await _summary(service, admin_id)


@router.patch("/admins/{admin_id}", response_model=AdminResponse)
async def update_admin(
    admin_id: int,
    payload: AdminUpdateRequest,
    current: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> Admin:
    return await service.update(
        admin_id, name=payload.name, actor_principal_id=current.principal_id
    )


@router.put("/admins/{admin_id}/role", response_model=AdminResponse)
async def set_admin_role(
    admin_id: int,
    payload: AdminRoleUpdateRequest,
    current: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> Admin:
    return await service.set_admin_role(
        admin_id, admin_role=payload.admin_role, actor_principal_id=current.principal_id
    )


@router.post("/admins/{admin_id}/archive", response_model=AdminSummary)
async def archive_admin(
    admin_id: int,
    current: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> AdminSummary:
    await service.archive(admin_id, actor_principal_id=current.principal_id)
    return await _summary(service, admin_id)


@router.post("/admins/{admin_id}/unarchive", response_model=AdminSummary)
async def unarchive_admin(
    admin_id: int,
    _: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> AdminSummary:
    await service.unarchive(admin_id)
    return await _summary(service, admin_id)


@router.delete("/admins/{admin_id}", response_model=AdminSummary)
async def delete_admin(
    admin_id: int,
    current: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> AdminSummary:
    """軟刪除（狀態轉移）：回 200 + 更新後 AdminSummary（deleted_at 有值）。api §5。"""
    await service.delete(admin_id, actor_principal_id=current.principal_id)
    return await _summary(service, admin_id)


@router.post("/admins/{admin_id}/restore", response_model=AdminSummary)
async def restore_admin(
    admin_id: int,
    _: Admin = Depends(_require_super),
    service: AdminService = Depends(get_admin_service),
) -> AdminSummary:
    await service.restore(admin_id)
    return await _summary(service, admin_id)
