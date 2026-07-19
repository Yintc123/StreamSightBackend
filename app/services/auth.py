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
    verify_password_or_dummy,
)
from app.core.config import get_app_settings
from app.core.enums import Role
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import mask_email
from app.dtos import (
    AdminLoginRequest,
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

# bootstrap root 為真實 DB admin（無哨兵）：登入/授權全走一般路徑，不 import initial_admin。
from app.services.user import UserService
from app.services.ws.publisher import Publisher

logger: logging.Logger = logging.getLogger(__name__)

PASSWORD_PROVIDER: str = "password"


def _grade_of(child: User | Admin) -> int:
    """讀 child 的等級（int，rank=value）供 grade claim：admin→admin_role、user→user_tier（enum-int）。"""
    return child.admin_role if isinstance(child, Admin) else child.user_tier


def _as_utc(dt: datetime) -> datetime:
    """Normalize a DB-loaded datetime to aware-UTC.

    SQLite（測試環境）讀回的 datetime 是 naive；與 aware `now` 相減會 TypeError。
    見 docs/specs/refresh-token-rotation.md §3 時區陷阱。
    """
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class AuthService:
    """Register + login flows, identity-provider aware; refresh token rotation."""

    def __init__(self, session: AsyncSession, publisher: Publisher | None = None) -> None:
        self.session: AsyncSession = session
        self.user_service: UserService = UserService(session)
        self.identity_repo: IdentityRepository = IdentityRepository(session)
        self.principal_repo: PrincipalRepository = PrincipalRepository(session)
        self.admin_repo: AdminRepository = AdminRepository(session)
        self.refresh_repo: RefreshTokenRepository = RefreshTokenRepository(session)
        # 登出時即時斷 WS（best-effort kick；可靠性由 WS 定期複查兜底，websocket §2.5）。
        self.publisher: Publisher | None = publisher

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
            # 4. 產 access token + refresh token（sub = principal_id、role = 0、grade = user_tier）
            #    sid = 當次 refresh family_id（供 WS 綁 session、單一 logout，見 websocket §2.11）
            family_id: str = str(uuid4())
            access_token: str = create_access_token(
                user.principal_id, Role.USER, grade=_grade_of(user), sid=family_id
            )
            refresh_token, _ = await self._issue_refresh_token(user.principal_id, family_id)
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
        identity: Identity | None = None
        if user is not None:
            identity = await self.identity_repo.get_by_user_and_provider(user.id, PASSWORD_PROVIDER)

        # 常數時間：無論帳號/identity 是否存在都跑一次 argon2（None → 對 dummy hash），
        # 拉平時序防 email 列舉側通道（與 admin_login 一致）。
        password_ok = await verify_password_or_dummy(
            identity.credential if identity is not None else None,
            payload.password,
        )
        if user is None or identity is None or not password_ok:
            raise UnauthorizedError("Invalid email or password")

        # 停用帳號不發 token（與 admin_login 語意一致；沿用統一模糊訊息，見 §5.4）
        if not user.is_active:
            raise UnauthorizedError("Invalid email or password")

        family_id: str = str(uuid4())
        access_token: str = create_access_token(
            user.principal_id, Role.USER, grade=_grade_of(user), sid=family_id
        )
        refresh_token, _ = await self._issue_refresh_token(user.principal_id, family_id)
        await self.session.commit()

        # 低頻路徑順手清理過期 token（best-effort，不影響登入）
        await self._purge_expired_best_effort()

        logger.info("Login success user id=%s email=%s", user.id, mask_email(user.email or ""))
        return TokenPayload(access_token=access_token, refresh_token=refresh_token)

    async def admin_login(self, payload: AdminLoginRequest) -> TokenPayload:
        """Verify admin username + password → role 1 access + refresh token.

        共用 `_issue_refresh_token`（角色無關發放）；沿用統一模糊訊息防列舉。封存／軟刪除的
        admin 不發 token（讀 is_active 計算屬性）。常數時間 verify 防帳號列舉時序側通道（§5.4）。

        Raises:
            UnauthorizedError: username 不存在、密碼錯、或帳號封存／軟刪除（統一訊息）
        """
        # bootstrap root 為真實 DB admin（grade ROOT、is_protected）→ 走一般路徑，無哨兵特判。
        # DTO 已正規化 username（strip + lower）
        admin: Admin | None = await self.admin_repo.get_by_username(payload.username)
        # 常數時間：無論帳號是否存在都跑一次 argon2（None → 對 dummy hash），拉平時序防列舉。
        password_ok: bool = await verify_password_or_dummy(
            admin.password_hash if admin else None, payload.password
        )
        if admin is None or not password_ok or not admin.is_active:
            raise UnauthorizedError("Invalid username or password")

        family_id: str = str(uuid4())
        access_token: str = create_access_token(
            admin.principal_id, Role.ADMIN, grade=_grade_of(admin), sid=family_id
        )
        refresh_token, _ = await self._issue_refresh_token(admin.principal_id, family_id)
        await self.session.commit()

        logger.info("Admin login success id=%s username=%s", admin.id, admin.username)
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
        # 依 principal.role 重簽正確 role 的 access token（天然防提權）；順手讀 child 最新
        # 等級重簽 grade → 每次 rotation 自動刷新（陳舊窗口 ≤ 一個 access TTL，見 rbac §5.1）。
        # rotation 保持同一 family_id → sid 穩定（同一 session 跨多次 refresh 不變，websocket §2.11）
        new_access: str = create_access_token(
            rt.principal_id, role, grade=_grade_of(child), sid=rt.family_id
        )
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
        # 單一 logout：只斷該 session（sid = family_id）的 WS（websocket §2.5）。
        await self._kick_session(rt.family_id)

    async def logout_all(self, principal_id: int) -> None:
        """Revoke all active refresh tokens for a principal (logout all devices, 角色無關)."""
        await self.refresh_repo.revoke_all_for_principal(principal_id, datetime.now(UTC))
        await self.session.commit()
        # logout_all：斷該 principal 的全部 WS（websocket §2.5）。
        await self._kick_principal(principal_id)

    async def _kick_principal(self, principal_id: int) -> None:
        """best-effort：發佈 Redis kick 斷該 principal 全部 WS。失敗不影響已提交的登出。"""
        if self.publisher is None:
            return
        try:
            await self.publisher.disconnect_principal(principal_id)
        except Exception:
            logger.warning("WS kick publish failed principal=%s", principal_id, exc_info=True)

    async def _kick_session(self, family_id: str) -> None:
        """best-effort：發佈 Redis kick 只斷該 session（sid）的 WS。失敗由定期複查兜底。"""
        if self.publisher is None:
            return
        try:
            await self.publisher.disconnect_session(family_id)
        except Exception:
            logger.warning("WS kick publish failed sid=%s", family_id, exc_info=True)

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

        # bootstrap root 為真實 DB admin → 一般查詢，無 sub==0 合成分支。
        admin: Admin | None = await self.admin_repo.get_by_principal_id(principal_id)
        if admin is None or not admin.is_active:
            raise UnauthorizedError("Admin not found or inactive")

        return admin

    async def get_principal_from_token(self, token: str) -> CurrentPrincipal:
        """只驗簽 + 解析 (principal_id, role)，**不查 DB**（logout-all 等用，見 §5.6）。"""
        principal_id, role = self._decode_access(token)
        return CurrentPrincipal(id=principal_id, role=role)
