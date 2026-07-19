from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_current_user, get_user_service
from app.core.exceptions import ForbiddenError
from app.dtos import UserUpdate
from app.models.user import User
from app.services import UserService

from .schemas import UserResponse

# Swagger UI 分組標籤。同 tags 的 endpoint 在 /docs 會被組在一起
router: APIRouter = APIRouter(prefix="/users", tags=["users"])

# /users 為使用者自助資源：以本人 access token 存取，且只能存取自己（self-scoped，與 admin 無關）。
# 註冊走 /auth/register（建 user+identity+發 token）；本 router 不提供列表與建立。


def _ensure_self(user_id: int, current_user: User) -> None:
    """只能存取自己的帳號；非本人 → 403（不洩漏他人是否存在）。"""
    if user_id != current_user.id:
        raise ForbiddenError("Cannot access another user's account")


# FastAPI 匹配有順序性，要放在 /{user_id} 前，不然會匹配成 user_id="me"；Flask 則不會，Flask 優先匹配固定路由再匹配動態路由
@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user",
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Return the current authenticated user from the Bearer token."""
    return UserResponse.model_validate(current_user)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get own user by ID",
)
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    _ensure_self(user_id, current_user)
    # self：current_user 即為該筆，免再查一次 DB
    return UserResponse.model_validate(current_user)


@router.patch("/{user_id}", response_model=UserResponse, summary="Update own account (partial)")
async def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    _ensure_self(user_id, current_user)
    user: User = await service.update(current_user.id, payload)
    return UserResponse.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete own account",
)
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> None:
    _ensure_self(user_id, current_user)
    await service.delete(current_user.id)
