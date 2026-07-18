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


def create_access_token(
    subject: str | int,
    role: Role = Role.USER,
    grade: str | None = None,
    sid: str | None = None,
) -> str:
    """
    Create a JWT access token for the given subject (usually principal id).

    Payload claim:
        sub: subject (principal id, stringified)
        type: "access"
        role: 角色判別子（0=user, 1=admin）；預設 Role.USER 讓既有呼叫端無縫相容
        grade: 該型別內的等級字串（admin→admin_role、user→user_tier）；None 則不放此 key
               （向後相容，僅為 UX 提示、非授權邊界，見 rbac §4）
        sid: session id（= 該登入的 refresh family_id），供 WS 綁 session、單一 logout
             精準斷線（websocket §2.11）；None 則不放此 key（向後相容，比照 grade）。
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
    if grade is not None:
        payload["grade"] = grade
    if sid is not None:
        payload["sid"] = sid

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


def extract_grade(payload: dict[str, Any]) -> str | None:
    """解析 grade claim（該身分的等級字串）：缺 key → None。

    grade 僅為前端 UX 提示、非授權邊界（後端授權讀 child 現值）。見 rbac §4/§5.5。
    """
    return payload.get("grade")


def extract_sid(payload: dict[str, Any]) -> str | None:
    """解析 sid claim（session id＝refresh family_id）：缺 key → None。

    供 WS 綁 session、單一 logout 精準斷線（websocket §2.11）。無 sid 的 token
    （初始 admin／舊 token）→ 不參與 sid-kick，只受 principal 級 kick 與定期複查。
    """
    return payload.get("sid")


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
    "extract_grade",
    "extract_role",
    "extract_sid",
]
