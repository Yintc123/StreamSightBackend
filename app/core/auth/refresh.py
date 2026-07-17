"""
Refresh token 的 opaque token 產生與雜湊。

- refresh token 是不透明隨機字串（非 JWT），DB 為真實來源。
- DB 只存 HMAC-SHA256(pepper, token) 的 hex digest，不存明文；即使 DB 單獨外洩，
  缺少 pepper 也無法離線建表反查。pepper 來自 settings.refresh_token_hash_secret，
  與 jwt_secret_key 分離。
"""

import hashlib
import hmac
import secrets

from app.core.config import get_app_settings


def generate_refresh_token() -> str:
    """Generate a high-entropy opaque refresh token (~256 bits, URL-safe)."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """Return HMAC-SHA256(pepper, token) hex digest (64 chars) for DB storage/lookup."""
    pepper: bytes = get_app_settings().refresh_token_hash_secret.get_secret_value().encode()
    return hmac.new(pepper, token.encode(), hashlib.sha256).hexdigest()


__all__ = [
    "generate_refresh_token",
    "hash_refresh_token",
]
