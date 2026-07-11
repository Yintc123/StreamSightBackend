from fastapi import APIRouter, status, Depends

from app.api.dependencies import get_user_service
from app.dtos import UserCreate, UserUpdate
from app.services import UserService
from app.models.user import User

from .schemas import UserResponse

# Swagger UI 分組標籤。同 tags 的 endpoint 在 /docs 會被組在一起
router: APIRouter = APIRouter(prefix="/users", tags=["users"])

@router.post(
        "",
        response_model=UserResponse,
        status_code=status.HTTP_201_CREATED
)
async def create_user(
    payload: UserCreate,
    service: UserService = Depends(get_user_service)
) -> UserResponse:
    user: User = await service.create(payload)
    return UserResponse.model_validate(user)

@router.get(
    "",
    response_model=list[UserResponse],
    summary="List users",
)
async def list_users(
    service: UserService = Depends(get_user_service)
) -> list[UserResponse]:
    users: list[User] = await service.list_all()
    # response JSON array
    return [UserResponse.model_validate(u) for u in users]

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

@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update a user (partial)"
)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    service: UserService = Depends(get_user_service)
) -> UserResponse:
    user: User = await service.update(user_id, payload)
    return UserResponse.model_validate(user)

@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user",
)
async def delete_user(
    user_id: int,
    service: UserService = Depends(get_user_service)
) -> None:
    await service.delete(user_id)
