"""
JWT encode / decode helpers.
- 標準 claims：sub / iat / exp
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidTokenError,
)

from app.core.config import BaseAppSettings, get_app_settings
from app.core.enums import Role


def create_access_token(subject: str | int, role: Role = Role.USER) -> str:
    """
    Create a JWT access token for the given subject (usually principal id).

    Payload claim:
        sub: subject (principal id, stringified)
        type: "access"
        role: 角色判別子（0=user, 1=admin）；預設 Role.USER 讓既有呼叫端無縫相容
        iat: issued at (UTC timestamp)
        exp: expires at (UTC timestamp)
    """
    settings: BaseAppSettings = get_app_settings()
    now: datetime = datetime.now(UTC)
    expires_at: datetime = now + timedelta(seconds=settings.jwt_access_token_expire_seconds)

    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": "access",
        "role": int(role),
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def extract_role(payload: dict[str, Any]) -> Role:
    """Fail-safe 解析 role claim：缺 claim 或未知值都退回最低權限 Role.USER。

    只寫 `Role(payload.get("role", Role.USER))` 只處理「缺 claim」；若 claim 是未知
    整數（未來版本簽出的 role=2 被舊 server 解到），`Role(2)` 會 ValueError → 500。
    降到最低權限與 fail-safe 授權一致。見 docs/specs/jwt-role-and-admin.md §5.1。
    """
    try:
        return Role(payload.get("role", Role.USER))
    except ValueError:
        return Role.USER


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode + verify a JWT

    Raises:
        ExpiredSignatureError: token 的 exp 已過期
        InvalidTokenError: 簽名錯誤 / 格式不正確 / JWT 被篡改
    """
    settings: BaseAppSettings = get_app_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )


__all__ = [
    "ExpiredSignatureError",
    "InvalidTokenError",
    "create_access_token",
    "decode_token",
    "extract_role",
]
