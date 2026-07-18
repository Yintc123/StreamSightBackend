"""Unit tests for AuthService — register / login / token → user."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.config import get_app_settings
from app.core.enums import Role
from app.core.exceptions import ConflictError, UnauthorizedError
from app.dtos import LoginRequest, RegisterRequest
from app.models import Principal, User
from app.repositories.principal import PrincipalRepository
from app.services import AuthService


async def _count_principals(db_session: AsyncSession) -> int:
    return (await db_session.execute(select(func.count()).select_from(Principal))).scalar_one()


async def _offset_principal_sequence(db_session: AsyncSession) -> None:
    """建一個獨立 principal 讓 principals 與 users 的自增序列錯開，

    使 principal_id != user.id，才能區分 sub 用的是 principal_id 而非 user.id。
    """
    await PrincipalRepository(db_session).create(Role.ADMIN)
    await db_session.commit()


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


async def test_register_token_sub_is_principal_id_with_user_role(db_session: AsyncSession) -> None:
    """access token 的 sub = principal_id（非 user.id）、role claim = 0。"""
    await _offset_principal_sequence(db_session)
    auth: AuthService = AuthService(db_session)

    resp = await auth.register(
        RegisterRequest(email="psub@example.com", name="P", password="longpassword")
    )

    user: User | None = await auth.user_service.repo.get_by_email("psub@example.com")
    assert user is not None
    assert user.principal_id != user.id  # 序列已錯開，區分得出 sub 用哪個
    payload: dict = decode_token(resp.access_token)
    assert payload["sub"] == str(user.principal_id)
    assert payload["role"] == 0


async def test_login_token_sub_is_principal_id_with_user_role(db_session: AsyncSession) -> None:
    await _offset_principal_sequence(db_session)
    auth: AuthService = AuthService(db_session)
    await auth.register(
        RegisterRequest(email="lsub@example.com", name="L", password="longpassword")
    )

    resp = await auth.login(LoginRequest(email="lsub@example.com", password="longpassword"))

    user: User | None = await auth.user_service.repo.get_by_email("lsub@example.com")
    assert user is not None
    assert user.principal_id != user.id
    payload: dict = decode_token(resp.access_token)
    assert payload["sub"] == str(user.principal_id)
    assert payload["role"] == 0


async def test_login_inactive_user_raises_unauthorized(db_session: AsyncSession) -> None:
    """停用帳號登入（正確帳密）→ UnauthorizedError（統一訊息），不發 token。"""
    auth: AuthService = AuthService(db_session)
    await auth.register(
        RegisterRequest(email="ialogin@example.com", name="IA", password="longpassword")
    )
    user: User | None = await auth.user_service.repo.get_by_email("ialogin@example.com")
    assert user is not None
    user.is_active = False
    await db_session.commit()

    with pytest.raises(UnauthorizedError) as exc:
        await auth.login(LoginRequest(email="ialogin@example.com", password="longpassword"))
    assert exc.value.message == "Invalid email or password"


async def test_get_user_from_token_resolves_by_principal_id(db_session: AsyncSession) -> None:
    """sub=principal_id 時，get_user_from_token 依 principal_id 解析出正確 user。"""
    await _offset_principal_sequence(db_session)
    auth: AuthService = AuthService(db_session)
    resp = await auth.register(
        RegisterRequest(email="resolve@example.com", name="R", password="longpassword")
    )

    user: User = await auth.get_user_from_token(resp.access_token)

    assert user.email == "resolve@example.com"
    assert user.principal_id != user.id


async def test_register_mid_failure_leaves_no_residue(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """交易原子性（UoW）：register 中途失敗（identity 建立拋例外）→ 資料庫零殘留。

    釘死 §5.4 UoW：principal + user + identity + refresh 必須同一 commit 落地，
    失敗整批 rollback；不得留下孤兒 principal（舊多段 commit 會殘留）。
    """
    auth: AuthService = AuthService(db_session)
    email: str = "residue@example.com"
    principals_before: int = await _count_principals(db_session)

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("identity insert failed")

    monkeypatch.setattr(auth.identity_repo, "add", _boom)

    with pytest.raises(RuntimeError):
        await auth.register(RegisterRequest(email=email, name="R", password="longpassword"))

    # 零殘留：無 user、無孤兒 principal、無 refresh token
    assert await auth.user_service.repo.get_by_email(email) is None
    assert await _count_principals(db_session) == principals_before
    assert (
        await db_session.execute(select(func.count()).select_from(User).where(User.email == email))
    ).scalar_one() == 0


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


async def test_login_nonexistent_email_still_calls_dummy_verify(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """email 不存在時，仍呼叫 verify_password_or_dummy（常數時間防 email 列舉時序側通道）。"""
    calls: list[str | None] = []

    async def _fake_verify(stored_hash: str | None, plain: str) -> bool:
        calls.append(stored_hash)
        return False

    monkeypatch.setattr("app.services.auth.verify_password_or_dummy", _fake_verify)

    auth = AuthService(db_session)
    with pytest.raises(UnauthorizedError):
        await auth.login(LoginRequest(email="nobody@example.com", password="any"))

    assert len(calls) == 1, "verify_password_or_dummy 應被呼叫一次（常數時間）"
    assert calls[0] is None, "stored_hash 應為 None（對 dummy hash 跑）"


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
