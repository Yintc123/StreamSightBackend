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
    extract_grade,
    extract_role,
    extract_sid,
)
from app.core.config import BaseAppSettings, get_app_settings
from app.core.enums import Role

user_id: int = 42


def test_create_and_decode_roundtrip() -> None:
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert payload["sub"] == str(user_id)


def test_token_has_expected_claims() -> None:
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert set(payload.keys()) == {"sub", "type", "role", "iat", "exp"}


def test_access_token_default_role_is_user() -> None:
    """不傳 role → 預設 role claim 為 0（Role.USER），不破壞既有呼叫端。"""
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert payload["role"] == 0


def test_access_token_admin_role_claim() -> None:
    token: str = create_access_token(user_id, Role.ADMIN)
    payload: dict = decode_token(token)

    assert payload["role"] == 1


def test_grade_claim_included_when_provided() -> None:
    """rbac §4：帶 grade → payload 有 grade key（前端讀等級）。"""
    token: str = create_access_token(user_id, Role.ADMIN, grade=50)
    payload: dict = decode_token(token)

    assert payload["grade"] == 50
    assert set(payload.keys()) == {"sub", "type", "role", "grade", "iat", "exp"}


def test_grade_claim_omitted_when_none() -> None:
    """不傳 grade → 不放 grade key（向後相容，既有呼叫端無感）。"""
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert "grade" not in payload


def test_extract_grade_missing_returns_none() -> None:
    assert extract_grade({}) is None


def test_extract_grade_reads_claim() -> None:
    assert extract_grade({"grade": 100}) == 100


def test_sid_claim_included_when_provided() -> None:
    """§2.11：帶 sid → payload 有 sid key（= refresh family_id，供 WS 綁 session）。"""
    token: str = create_access_token(user_id, Role.ADMIN, grade=50, sid="fam-123")
    payload: dict = decode_token(token)

    assert payload["sid"] == "fam-123"
    assert set(payload.keys()) == {"sub", "type", "role", "grade", "sid", "iat", "exp"}


def test_sid_claim_omitted_when_none() -> None:
    """不傳 sid → 不放 sid key（向後相容，比照 grade 處理）。"""
    token: str = create_access_token(user_id)
    payload: dict = decode_token(token)

    assert "sid" not in payload


def test_extract_sid_missing_returns_none() -> None:
    assert extract_sid({}) is None


def test_extract_sid_reads_claim() -> None:
    assert extract_sid({"sid": "fam-abc"}) == "fam-abc"


def test_extract_role_missing_defaults_to_user() -> None:
    """缺 role claim → fail-safe 最低權限 Role.USER。"""
    assert extract_role({}) is Role.USER


def test_extract_role_reads_claim() -> None:
    assert extract_role({"role": 0}) is Role.USER
    assert extract_role({"role": 1}) is Role.ADMIN


def test_extract_role_unknown_value_fails_safe_to_user() -> None:
    """未知整數（如未來版本簽出 role=2）→ 降權而非 500。"""
    assert extract_role({"role": 2}) is Role.USER


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


def test_create_access_token_custom_expire_seconds() -> None:
    """expire_seconds 覆寫 → exp 約等於 now + 指定秒數（±5s 容差）。"""
    expire = 7200
    before = datetime.now(UTC)
    token: str = create_access_token(user_id, expire_seconds=expire)
    payload: dict = decode_token(token)

    expected_exp = before + timedelta(seconds=expire)
    actual_exp = datetime.fromtimestamp(payload["exp"], UTC)
    assert abs((actual_exp - expected_exp).total_seconds()) < 5
