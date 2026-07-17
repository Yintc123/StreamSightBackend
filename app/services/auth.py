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
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    ExpiredSignatureError,
    InvalidTokenError,
    create_access_token,
    decode_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.core.config import get_app_settings
from app.core.exceptions import UnauthorizedError
from app.core.security import mask_email
from app.dtos import LoginRequest, RefreshRequest, RegisterRequest, TokenPayload, UserCreate
from app.models import Identity, RefreshToken, User
from app.repositories.identity import IdentityRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.services.user import UserService

logger: logging.Logger = logging.getLogger(__name__)

PASSWORD_PROVIDER: str = "password"


def _as_utc(dt: datetime) -> datetime:
    """Normalize a DB-loaded datetime to aware-UTC.

    SQLite（測試環境）讀回的 datetime 是 naive；與 aware `now` 相減會 TypeError。
    見 docs/specs/refresh-token-rotation.md §3 時區陷阱。
    """
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class AuthService:
    """Register + login flows, identity-provider aware; refresh token rotation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session
        self.user_service: UserService = UserService(session)
        self.identity_repo: IdentityRepository = IdentityRepository(session)
        self.refresh_repo: RefreshTokenRepository = RefreshTokenRepository(session)

    async def _issue_refresh_token(self, user_id: int, family_id: str) -> tuple[str, RefreshToken]:
        """Create + persist a refresh token row; return (plaintext, row).

        呼叫端負責 commit。回傳 row 讓 rotation 能把舊 token 的 replaced_by_id 指向新 token。
        """
        settings = get_app_settings()
        plaintext: str = generate_refresh_token()
        expires_at: datetime = datetime.now(UTC) + timedelta(
            seconds=settings.refresh_token_expire_seconds
        )
        row: RefreshToken = RefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token(plaintext),
            family_id=family_id,
            expires_at=expires_at,
        )
        await self.refresh_repo.add(row)  # flush → row.id
        return plaintext, row

    async def _purge_expired_best_effort(self) -> None:
        """Opportunistic cleanup of expired tokens; failure must not break the caller."""
        try:
            await self.refresh_repo.delete_expired(datetime.now(UTC))
            await self.session.commit()
        except Exception:
            logger.warning("Refresh token cleanup failed", exc_info=True)
            await self.session.rollback()

    async def register(self, payload: RegisterRequest) -> TokenPayload:
        """Register a new user + password identity + return access & refresh token (auto-login).

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

        # 4. 產 access token + refresh token (user/identity 已 commit，此段失敗僅影響 refresh)
        access_token: str = create_access_token(user.id)
        refresh_token, _ = await self._issue_refresh_token(user.id, str(uuid4()))
        await self.session.commit()

        logger.info("Registered user id=%s email=%s", user.id, mask_email(user.email or ""))
        return TokenPayload(access_token=access_token, refresh_token=refresh_token)

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

        access_token: str = create_access_token(user.id)
        refresh_token, _ = await self._issue_refresh_token(user.id, str(uuid4()))
        await self.session.commit()

        # 低頻路徑順手清理過期 token（best-effort，不影響登入）
        await self._purge_expired_best_effort()

        logger.info("Login success user id=%s email=%s", user.id, mask_email(user.email or ""))
        return TokenPayload(access_token=access_token, refresh_token=refresh_token)

    async def refresh(self, payload: RefreshRequest) -> TokenPayload:
        """Rotate a refresh token: revoke the old, issue new access + refresh.

        Raises:
            UnauthorizedError: token unknown / revoked (reuse) / expired / user inactive
        """
        settings = get_app_settings()
        now: datetime = datetime.now(UTC)
        grace: timedelta = timedelta(seconds=settings.refresh_token_reuse_grace_seconds)

        rt: RefreshToken | None = await self.refresh_repo.get_by_hash(
            hash_refresh_token(payload.refresh_token)
        )

        # 1. 查無此 token
        if rt is None:
            raise UnauthorizedError("Invalid refresh token")

        # 2. 已撤銷 → reuse 判定（含 grace）
        if rt.revoked_at is not None:
            if now - _as_utc(rt.revoked_at) <= grace:
                # 剛輪替的良性並發/重試：只 401、不連坐 family、無寫入
                raise UnauthorizedError("Invalid refresh token")
            # 真正的舊 token 重用：撤銷整個 family（commit 務必在 raise 前）
            await self.refresh_repo.revoke_family(rt.family_id, now)
            await self.session.commit()
            raise UnauthorizedError("Invalid refresh token")

        # 3. 已過期
        if _as_utc(rt.expires_at) <= now:
            raise UnauthorizedError("Refresh token expired")

        # 4. 先驗 user（寫入前，避免對停用 user 留下孤兒 active token）
        user: User | None = await self.user_service.repo.get(rt.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("User not found or inactive")

        # 5. 原子消費：先發新 token 取得 id，再原子撤銷舊 token 並指向新 token
        new_plain, new_row = await self._issue_refresh_token(rt.user_id, rt.family_id)
        won: bool = await self.refresh_repo.consume(
            rt.id, revoked_at=now, replaced_by_id=new_row.id
        )
        if not won:
            # 並發下已被別的請求消費：rollback 撤掉剛才多發的 new_row
            await self.session.rollback()
            raise UnauthorizedError("Invalid refresh token")

        await self.session.commit()
        new_access: str = create_access_token(rt.user_id)
        logger.info("Refreshed token for user id=%s", rt.user_id)
        return TokenPayload(access_token=new_access, refresh_token=new_plain)

    async def logout(self, payload: RefreshRequest) -> None:
        """Revoke the presented refresh token. Silent if unknown/already revoked."""
        rt: RefreshToken | None = await self.refresh_repo.get_by_hash(
            hash_refresh_token(payload.refresh_token)
        )
        if rt is None or rt.revoked_at is not None:
            return  # 靜默成功，避免 enumeration
        rt.revoked_at = datetime.now(UTC)
        await self.session.commit()

    async def logout_all(self, user_id: int) -> None:
        """Revoke all active refresh tokens for a user (logout all devices)."""
        await self.refresh_repo.revoke_all_for_user(user_id, datetime.now(UTC))
        await self.session.commit()

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
