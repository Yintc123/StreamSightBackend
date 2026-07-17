from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_auth_service, get_current_principal
from app.core.config import get_app_settings
from app.dtos import CurrentPrincipal, LoginRequest, RefreshRequest, RegisterRequest, TokenPayload
from app.services import AuthService

from .schemas import TokenResponse

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


def _to_token_response(token: TokenPayload) -> TokenResponse:
    """Wrap a service TokenPayload into the API TokenResponse, filling expires_in."""
    return TokenResponse(
        **token.model_dump(),
        expires_in=get_app_settings().jwt_access_token_expire_seconds,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Register a new user and return an access token (auto-login)."""
    token: TokenPayload = await service.register(payload)
    return _to_token_response(token)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Verify credentials and return an access token."""
    token: TokenPayload = await service.login(payload)
    return _to_token_response(token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Rotate a refresh token: return a new access + refresh token."""
    token: TokenPayload = await service.refresh(payload)
    return _to_token_response(token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest,
    service: AuthService = Depends(get_auth_service),
) -> None:
    """Revoke the presented refresh token (idempotent)."""
    await service.logout(payload)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    principal: CurrentPrincipal = Depends(get_current_principal),
    service: AuthService = Depends(get_auth_service),
) -> None:
    """Revoke all refresh tokens for the current principal (角色無關；user / admin 皆可)."""
    await service.logout_all(principal.id)
