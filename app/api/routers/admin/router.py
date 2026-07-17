from fastapi import APIRouter, Depends

from app.api.dependencies import get_auth_service, get_current_admin
from app.api.routers.auth.schemas import TokenResponse
from app.core.config import get_app_settings
from app.dtos import AdminLoginRequest, TokenPayload
from app.models import Admin
from app.services import AuthService

from .schemas import AdminResponse

router: APIRouter = APIRouter(prefix="/admin", tags=["admin"])


def _to_token_response(token: TokenPayload) -> TokenResponse:
    """Wrap a service TokenPayload into the API TokenResponse, filling expires_in."""
    return TokenResponse(
        **token.model_dump(),
        expires_in=get_app_settings().jwt_access_token_expire_seconds,
    )


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
