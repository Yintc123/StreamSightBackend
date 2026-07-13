"""Unit tests for JWT helpers."""

import warnings
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.auth import (
    ExpiredSignatureError,
    InvalidTokenError,
    create_access_token,
    decode_token,
)
from app.core.config import BaseAppSettings, get_app_settings

user_id: int = 42


def test_create_and_decode_roundtrip() -> None:
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert payload["sub"] == str(user_id)


def test_token_has_expected_claims() -> None:
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert set(payload.keys()) == {"sub", "type", "iat", "exp"}


def test_sub_is_stringified() -> None:
    """JWT spec: sub 是字串，即使傳入 int 也要轉成 str。"""
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert isinstance(payload["sub"], str)
    assert payload["sub"] == str(user_id)


def test_type_claim_is_access() -> None:
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert payload["type"] == "access"


def test_expired_token_raises() -> None:
    settings: BaseAppSettings = get_app_settings()

    # 手動簽已過期的 token
    expired_payload: dict = {
        "sub": str(user_id),
        "type": "access",
        "iat": datetime.now(UTC) - timedelta(seconds=60 * 60 * 60),
        "exp": datetime.now(UTC) - timedelta(seconds=30 * 60),
    }

    expired_token: str = jwt.encode(
        expired_payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(ExpiredSignatureError):
        decode_token(expired_token)


def test_tampered_token_raises() -> None:
    """改動 signature -> InvalidTokenError

    注意：不能只改 signature 最後一個 char — 因為 base64url 最後一個 char
    只用了前 4 bits、後 2 bits 是 padding、換符合的 char 可能 decode 出相同 bytes、
    導致 signature 驗證仍過（flaky test）。改中間的 chars 才穩定。
    """
    token: str = create_access_token(user_id)
    header, payload, signature = token.split(".")
    # 換掉 signature 中間 5 chars、保證 decoded bytes 一定不同
    tampered_sig: str = signature[:5] + "XXXXX" + signature[10:]
    tampered: str = f"{header}.{payload}.{tampered_sig}"

    with pytest.raises(InvalidTokenError):
        decode_token(tampered)


def test_wrong_algorithm_raises() -> None:
    """用 HS512 簽 token (config 只允許 HS256) → decode_token 拒收。

    HS512 的 InsecureKeyLengthWarning：test key 49 bytes、HS512 建議 >=64 bytes。
    這 test 的意圖是「算法白名單」、key 長度無關、抑制掉。
    """
    settings: BaseAppSettings = get_app_settings()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wrong_alg_token: str = jwt.encode(
            {"sub": str(user_id), "type": "access"},
            settings.jwt_secret_key.get_secret_value(),
            algorithm="HS512",  # 用不同算法
        )

    with pytest.raises(InvalidTokenError):
        decode_token(wrong_alg_token)
