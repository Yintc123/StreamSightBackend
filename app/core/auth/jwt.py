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


def create_access_token(subject: str | int) -> str:
    """
    Create a JWT access token for the given subject (usually user id).

    Payload claim:
        sub: subject (user id, stringified)
        type: "acess"
        iat: issued at (UTC timestamp)
        exp: expires at (UTC timestamp)
    """
    settings: BaseAppSettings = get_app_settings()
    now: datetime = datetime.now(UTC)
    expires_at: datetime = now + timedelta(seconds=settings.jwt_access_token_expire_seconds)

    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": "access",
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


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
]
