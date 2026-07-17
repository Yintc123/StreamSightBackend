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
    extract_role,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.core.config import get_app_settings
from app.core.enums import Role
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import mask_email
from app.dtos import (
    CurrentPrincipal,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPayload,
    UserCreate,
)
from app.models import Admin, Identity, Principal, RefreshToken, User
from app.repositories.admin import AdminRepository
from app.repositories.identity import IdentityRepository
from app.repositories.principal import PrincipalRepository
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
        self.principal_repo: PrincipalRepository = PrincipalRepository(session)
        self.admin_repo: AdminRepository = AdminRepository(session)
        self.refresh_repo: RefreshTokenRepository = RefreshTokenRepository(session)

    async def _issue_refresh_token(
        self, principal_id: int, family_id: str
    ) -> tuple[str, RefreshToken]:
        """Create + persist a refresh token row; return (plaintext, row).

        擁有者為 principal_id（角色無關）。呼叫端負責 commit。回傳 row 讓 rotation
        能把舊 token 的 replaced_by_id 指向新 token。
        """
        settings = get_app_settings()
        plaintext: str = generate_refresh_token()
        expires_at: datetime = datetime.now(UTC) + timedelta(
            seconds=settings.refresh_token_expire_seconds
        )
        row: RefreshToken = RefreshToken(
            principal_id=principal_id,
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

        Unit-of-Work：principal + user + identity + refresh 在**同一 commit** 原子落地，
        任一步失敗整批 rollback（不留孤兒 principal）。見 D10 / §5.4。

        Raises:
            ConflictError: email already taken (from UserService.build)
        """
        # 1. hash password (threadpool、不阻塞 event loop)
        password_hash: str = await hash_password(payload.password)

        try:
            # 2. 建 principal + user（只 flush，不 commit）
            user: User = await self.user_service.build(
                UserCreate(email=payload.email, name=payload.name)
            )
            # 3. 建 password identity（只 flush）
            identity: Identity = Identity(
                user_id=user.id,
                provider=PASSWORD_PROVIDER,
                credential=password_hash,
            )
            await self.identity_repo.add(identity)
            # 4. 產 access token + refresh token（sub = principal_id、role = 0）
            access_token: str = create_access_token(user.principal_id, Role.USER)
            refresh_token, _ = await self._issue_refresh_token(user.principal_id, str(uuid4()))
            # 5. 唯一一次 commit：全部原子落地
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

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

        # 停用帳號不發 token（與 admin_login 語意一致；沿用統一模糊訊息，見 §5.4）
        if not user.is_active:
            raise UnauthorizedError("Invalid email or password")

        access_token: str = create_access_token(user.principal_id, Role.USER)
        refresh_token, _ = await self._issue_refresh_token(user.principal_id, str(uuid4()))
        await self.session.commit()

        # 低頻路徑順手清理過期 token（best-effort，不影響登入）
        await self._purge_expired_best_effort()

        logger.info("Login success user id=%s email=%s", user.id, mask_email(user.email or ""))
        return TokenPayload(access_token=access_token, refresh_token=refresh_token)

    async def admin_login(self, payload: LoginRequest) -> TokenPayload:
        """Verify admin email + password → role 1 access + refresh token.

        共用 `_issue_refresh_token`（角色無關發放）；沿用統一模糊訊息防列舉。停用 admin 不發 token。

        Raises:
            UnauthorizedError: email 不存在、密碼錯、或帳號停用（統一訊息）
        """
        admin: Admin | None = await self.admin_repo.get_by_email(payload.email)
        # 統一錯誤訊息防 enumeration：不存在也走 verify（此處 admin 不存在直接統一回覆）
        if admin is None or not await verify_password(payload.password, admin.password_hash):
            raise UnauthorizedError("Invalid email or password")
        if not admin.is_active:
            raise UnauthorizedError("Invalid email or password")

        access_token: str = create_access_token(admin.principal_id, Role.ADMIN)
        refresh_token, _ = await self._issue_refresh_token(admin.principal_id, str(uuid4()))
        await self.session.commit()

        logger.info("Admin login success id=%s email=%s", admin.id, mask_email(admin.email))
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

        # 4. 先驗 principal + child（寫入前，避免對停用/不存在帳號留下孤兒 active token）。
        #    refresh 無 role claim → 查父表定型別，再依 role 載對應 child 讀 is_active、重簽正確 role。
        principal: Principal | None = await self.principal_repo.get(rt.principal_id)
        if principal is None:
            raise UnauthorizedError("User not found or inactive")
        role: Role = Role(principal.role)
        child = await self._load_active_child(role, rt.principal_id)
        if child is None or not child.is_active:
            raise UnauthorizedError("User not found or inactive")

        # 5. 原子消費：先發新 token 取得 id，再原子撤銷舊 token 並指向新 token
        new_plain, new_row = await self._issue_refresh_token(rt.principal_id, rt.family_id)
        won: bool = await self.refresh_repo.consume(
            rt.id, revoked_at=now, replaced_by_id=new_row.id
        )
        if not won:
            # 並發下已被別的請求消費：rollback 撤掉剛才多發的 new_row
            await self.session.rollback()
            raise UnauthorizedError("Invalid refresh token")

        await self.session.commit()
        # 依 principal.role 重簽正確 role 的 access token（天然防提權）
        new_access: str = create_access_token(rt.principal_id, role)
        logger.info("Refreshed token for principal id=%s role=%s", rt.principal_id, int(role))
        return TokenPayload(access_token=new_access, refresh_token=new_plain)

    async def _load_active_child(self, role: Role, principal_id: int) -> User | Admin | None:
        """依 role 載入對應 child（讀本地 is_active）。user/admin 共用 refresh 角色無關路徑。"""
        if role is Role.USER:
            return await self.user_service.repo.get_by_principal_id(principal_id)
        return await self.admin_repo.get_by_principal_id(principal_id)

    async def logout(self, payload: RefreshRequest) -> None:
        """Revoke the presented refresh token. Silent if unknown/already revoked."""
        rt: RefreshToken | None = await self.refresh_repo.get_by_hash(
            hash_refresh_token(payload.refresh_token)
        )
        if rt is None or rt.revoked_at is not None:
            return  # 靜默成功，避免 enumeration
        rt.revoked_at = datetime.now(UTC)
        await self.session.commit()

    async def logout_all(self, principal_id: int) -> None:
        """Revoke all active refresh tokens for a principal (logout all devices, 角色無關)."""
        await self.refresh_repo.revoke_all_for_principal(principal_id, datetime.now(UTC))
        await self.session.commit()

    def _decode_access(self, token: str) -> tuple[int, Role]:
        """驗簽 + 檢 type=access，回 (principal_id, role)。role fail-safe（缺/未知 → USER）。

        Raises:
            UnauthorizedError: token expired / invalid / wrong type / bad subject
        """
        try:
            payload: dict = decode_token(token)
        except ExpiredSignatureError as e:
            raise UnauthorizedError("Token has expired") from e
        except InvalidTokenError as e:
            raise UnauthorizedError("Invalid token") from e

        if payload.get("type") != "access":
            raise UnauthorizedError("Invalid token type")

        try:
            principal_id: int = int(payload["sub"])
        except (KeyError, ValueError) as e:
            raise UnauthorizedError("Invalid token subject") from e

        return principal_id, extract_role(payload)

    async def get_user_from_token(self, token: str) -> User:
        """Decode + validate token + return the User it represents (role 必須 0).

        Used by `get_current_user` FastAPI dependency.

        Raises:
            UnauthorizedError: token expired / invalid / user gone / wrong type
            ForbiddenError: 已認證但非 user 角色（admin token 打 user 端點）
        """
        principal_id, role = self._decode_access(token)
        if role is not Role.USER:
            raise ForbiddenError("User role required")

        # 依 principal_id 解析 user（sub = principal_id，見 §5.6）。
        # 用 repo（回 None）而非 service.get（raise NotFoundError）：
        # 「user 已刪除」與「user 已停用」都統一回 401（不透露 user 是否存在）。
        user: User | None = await self.user_service.repo.get_by_principal_id(principal_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("User not found or inactive")

        return user

    async def get_admin_from_token(self, token: str) -> Admin:
        """Decode + validate token + return the Admin it represents (role 必須 1).

        Raises:
            UnauthorizedError: token expired / invalid / admin gone / inactive / wrong type
            ForbiddenError: 已認證但非 admin 角色（user token 打 admin 端點）
        """
        principal_id, role = self._decode_access(token)
        if role is not Role.ADMIN:
            raise ForbiddenError("Admin role required")

        admin: Admin | None = await self.admin_repo.get_by_principal_id(principal_id)
        if admin is None or not admin.is_active:
            raise UnauthorizedError("Admin not found or inactive")

        return admin

    async def get_principal_from_token(self, token: str) -> CurrentPrincipal:
        """只驗簽 + 解析 (principal_id, role)，**不查 DB**（logout-all 等用，見 §5.6）。"""
        principal_id, role = self._decode_access(token)
        return CurrentPrincipal(id=principal_id, role=role)
