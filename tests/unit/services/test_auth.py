"""Unit tests for AuthService — register / login / token → user."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.config import get_app_settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.dtos import LoginRequest, RegisterRequest
from app.models import User
from app.services import AuthService


async def test_register_creates_user_with_hashed_password_identity(
    db_session: AsyncSession,
) -> None:
    """Register 建立的 password identity 存 argon2 hash、絕非明文。"""
    email: str = "new@example.com"
    password: str = "longpassword"
    auth: AuthService = AuthService(db_session)

    await auth.register(RegisterRequest(email=email, name="New", password=password))

    user: User | None = await auth.user_service.repo.get_by_email(email)
    assert user is not None

    # 查 password identity
    identity = await auth.identity_repo.get_by_user_and_provider(user.id, "password")
    assert identity is not None
    assert identity.credential != password
    assert identity.credential.startswith("$argon2id$")


async def test_register_returns_valid_token(db_session: AsyncSession) -> None:
    """Register 回的 token 能被 decode、包含 access type + sub。"""
    auth: AuthService = AuthService(db_session)

    token_resp = await auth.register(
        RegisterRequest(email="tok@example.com", name="Tok", password="longpassword"),
    )

    payload: dict = decode_token(token_resp.access_token)
    assert payload["type"] == "access"
    assert "sub" in payload


async def test_register_duplicate_email_raises_conflict(db_session: AsyncSession) -> None:
    auth: AuthService = AuthService(db_session)
    payload = RegisterRequest(email="dup@example.com", name="A", password="longpassword")

    await auth.register(payload)

    with pytest.raises(ConflictError):
        await auth.register(payload)


async def test_login_with_correct_password_returns_token(db_session: AsyncSession) -> None:
    auth: AuthService = AuthService(db_session)
    await auth.register(
        RegisterRequest(email="login@example.com", name="L", password="longpassword"),
    )

    token_resp = await auth.login(
        LoginRequest(email="login@example.com", password="longpassword"),
    )

    payload: dict = decode_token(token_resp.access_token)
    assert payload["type"] == "access"


async def test_login_with_wrong_password_raises_unauthorized(db_session: AsyncSession) -> None:
    auth: AuthService = AuthService(db_session)
    await auth.register(
        RegisterRequest(email="wp@example.com", name="W", password="longpassword"),
    )

    with pytest.raises(UnauthorizedError) as exc:
        await auth.login(LoginRequest(email="wp@example.com", password="WRONG_PASSWORD"))

    # 統一訊息、防 user enumeration
    assert exc.value.message == "Invalid email or password"


async def test_login_with_nonexistent_email_raises_unauthorized(db_session: AsyncSession) -> None:
    """防 user enumeration：email 不存在跟密碼錯應該同樣訊息。"""
    auth: AuthService = AuthService(db_session)

    with pytest.raises(UnauthorizedError) as exc:
        await auth.login(LoginRequest(email="nobody@example.com", password="anypassword"))

    assert exc.value.message == "Invalid email or password"


async def test_get_user_from_valid_token_returns_user(db_session: AsyncSession) -> None:
    auth: AuthService = AuthService(db_session)
    token_resp = await auth.register(
        RegisterRequest(email="me@example.com", name="Me", password="longpassword"),
    )

    user: User = await auth.get_user_from_token(token_resp.access_token)

    assert user.email == "me@example.com"


async def test_get_user_from_expired_token_raises_unauthorized(db_session: AsyncSession) -> None:
    """過期 token → UnauthorizedError（message 提到 expired）。"""
    auth: AuthService = AuthService(db_session)
    await auth.register(
        RegisterRequest(email="exp@example.com", name="E", password="longpassword"),
    )

    settings = get_app_settings()
    expired_token: str = jwt.encode(
        {
            "sub": "1",
            "type": "access",
            "iat": datetime.now(UTC) - timedelta(hours=1),
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(UnauthorizedError) as exc:
        await auth.get_user_from_token(expired_token)
    assert "expired" in exc.value.message.lower()


async def test_get_user_from_invalid_token_raises_unauthorized(db_session: AsyncSession) -> None:
    """亂 token → UnauthorizedError。"""
    auth: AuthService = AuthService(db_session)

    with pytest.raises(UnauthorizedError):
        await auth.get_user_from_token("not.a.real.token")


async def test_get_user_from_wrong_type_token_raises_unauthorized(db_session: AsyncSession) -> None:
    """Future-proof for refresh token：type != 'access' 應被 access endpoint 拒收。"""
    auth: AuthService = AuthService(db_session)
    settings = get_app_settings()

    refresh_token: str = jwt.encode(
        {
            "sub": "1",
            "type": "refresh",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(days=30),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(UnauthorizedError) as exc:
        await auth.get_user_from_token(refresh_token)
    assert "type" in exc.value.message.lower()


async def test_get_user_from_token_of_inactive_user_raises_unauthorized(
    db_session: AsyncSession,
) -> None:
    """User 被停用後、token 還沒過期也不能通過 auth。"""
    auth: AuthService = AuthService(db_session)
    token_resp = await auth.register(
        RegisterRequest(email="inactive@example.com", name="Ina", password="longpassword"),
    )

    user: User | None = await auth.user_service.repo.get_by_email("inactive@example.com")
    assert user is not None
    user.is_active = False
    await db_session.commit()

    with pytest.raises(UnauthorizedError):
        await auth.get_user_from_token(token_resp.access_token)


async def test_get_user_from_token_of_deleted_user_raises_unauthorized(
    db_session: AsyncSession,
) -> None:
    """User 已被刪除、token 未過期 → 統一回 401（不透露 user 是否存在過）。"""
    auth: AuthService = AuthService(db_session)
    token_resp = await auth.register(
        RegisterRequest(email="deleted@example.com", name="D", password="longpassword"),
    )

    user: User | None = await auth.user_service.repo.get_by_email("deleted@example.com")
    assert user is not None
    await auth.user_service.repo.delete(user)
    await db_session.commit()

    with pytest.raises(UnauthorizedError):
        await auth.get_user_from_token(token_resp.access_token)
