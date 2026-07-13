from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_auth_service
from app.dtos import LoginRequest, RegisterRequest, TokenPayload
from app.services import AuthService

from .schemas import TokenResponse

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Register a new user and return an access token (auto-login)."""
    token: TokenPayload = await service.register(payload)
    return TokenResponse(**token.model_dump())


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Verify credentials and return an access token."""
    token: TokenPayload = await service.login(payload)
    return TokenResponse(**token.model_dump())
