"""AuthService — register / login / token → user、支援多 identity provider。

Design:
    - User 表:identity-agnostic、只放使用者資料
    - Identity 表:每個 (user, provider) 一筆 row、存 credential 或 OAuth sub

Register / Login 都走 identities:
    - Register:建 User + Identity(provider="password")
    - Login:找 user、找 Identity(user_id, "password")、verify credential

未來加 OAuth (Step 追加):
    - login_with_google(id_token):verify Google token → 找 Identity("google", sub)
      → 若沒對應 identity、建 User + Identity("google", sub) → 發 access token
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    ExpiredSignatureError,
    InvalidTokenError,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.exceptions import UnauthorizedError
from app.core.security import mask_email
from app.dtos import LoginRequest, RegisterRequest, TokenPayload, UserCreate
from app.models import Identity, User
from app.repositories.identity import IdentityRepository
from app.services.user import UserService

logger: logging.Logger = logging.getLogger(__name__)

PASSWORD_PROVIDER: str = "password"


class AuthService:
    """Register + login flows, identity-provider aware."""

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session
        self.user_service: UserService = UserService(session)
        self.identity_repo: IdentityRepository = IdentityRepository(session)

    async def register(self, payload: RegisterRequest) -> TokenPayload:
        """Register a new user + password identity + return access token (auto-login).

        Raises:
            ConflictError: email already taken (from UserService.create)
        """
        # 1. hash password (threadpool、不阻塞 event loop)
        password_hash: str = await hash_password(payload.password)

        # 2. 建 User (UserService.create commits)
        user: User = await self.user_service.create(
            UserCreate(email=payload.email, name=payload.name)
        )

        # 3. 建 password identity (單獨 transaction)
        identity: Identity = Identity(
            user_id=user.id,
            provider=PASSWORD_PROVIDER,
            credential=password_hash,
        )
        try:
            await self.identity_repo.add(identity)
            await self.session.commit()
        except Exception:
            # identity 建失敗、要 rollback (user 已存 DB、需要清掉)
            await self.session.rollback()
            await self.user_service.delete(user.id)
            raise

        # 4. 產 token
        token: str = create_access_token(user.id)
        logger.info("Registered user id=%s email=%s", user.id, mask_email(user.email or ""))
        return TokenPayload(access_token=token)

    async def login(self, payload: LoginRequest) -> TokenPayload:
        """Verify email + password + return access token.

        Raises:
            UnauthorizedError: email 不存在、無 password identity、或密碼錯 (統一訊息)
        """
        user: User | None = await self.user_service.repo.get_by_email(payload.email)
        if user is None:
            raise UnauthorizedError("Invalid email or password")

        identity: Identity | None = await self.identity_repo.get_by_user_and_provider(
            user.id, PASSWORD_PROVIDER
        )
        # 統一錯誤訊息:防 user enumeration (無 password identity 也回同一個)
        if identity is None or not await verify_password(payload.password, identity.credential):
            raise UnauthorizedError("Invalid email or password")

        token: str = create_access_token(user.id)
        logger.info("Login success user id=%s email=%s", user.id, mask_email(user.email or ""))
        return TokenPayload(access_token=token)

    async def get_user_from_token(self, token: str) -> User:
        """Decode + validate token + return the user it represents.

        Used by `get_current_user` FastAPI dependency.

        Raises:
            UnauthorizedError: token expired / invalid / user gone / wrong type
        """
        try:
            payload: dict = decode_token(token)
        except ExpiredSignatureError as e:
            raise UnauthorizedError("Token has expired") from e
        except InvalidTokenError as e:
            raise UnauthorizedError("Invalid token") from e

        # 檢查 type claim
        if payload.get("type") != "access":
            raise UnauthorizedError("Invalid token type")

        # sub 轉回 int
        try:
            user_id: int = int(payload["sub"])
        except (KeyError, ValueError) as e:
            raise UnauthorizedError("Invalid token subject") from e

        # 用 repo.get() (回 None) 而不是 service.get() (raise NotFoundError)
        # 這樣「user 已被刪除」和「user 已停用」都統一回 401 (不透露 user 是否存在)
        user: User | None = await self.user_service.repo.get(user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("User not found or inactive")

        return user
