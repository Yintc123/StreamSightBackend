from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_current_user, get_user_service
from app.dtos import UserCreate, UserUpdate
from app.models.user import User
from app.services import UserService

from .schemas import UserResponse

# Swagger UI 分組標籤。同 tags 的 endpoint 在 /docs 會被組在一起
router: APIRouter = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, service: UserService = Depends(get_user_service)
) -> UserResponse:
    user: User = await service.create(payload)
    return UserResponse.model_validate(user)


@router.get(
    "",
    response_model=list[UserResponse],
    summary="List users",
)
async def list_users(service: UserService = Depends(get_user_service)) -> list[UserResponse]:
    users: list[User] = await service.list_all()
    # response JSON array
    return [UserResponse.model_validate(u) for u in users]


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
    summary="Get a user by ID",
)
async def get_user(
    user_id: int,
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    user: User = await service.get(user_id)
    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse, summary="Update a user (partial)")
async def update_user(
    user_id: int, payload: UserUpdate, service: UserService = Depends(get_user_service)
) -> UserResponse:
    user: User = await service.update(user_id, payload)
    return UserResponse.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user",
)
async def delete_user(user_id: int, service: UserService = Depends(get_user_service)) -> None:
    await service.delete(user_id)
