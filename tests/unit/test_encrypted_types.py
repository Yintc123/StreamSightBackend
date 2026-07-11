"""Tests for DeterministicEncryptedString column type."""

import pytest

from app.core.db.types import DeterministicEncryptedString

_KEY: bytes = b"test-encryption-key-32-chars-min-length"


@pytest.fixture
def enc_type() -> DeterministicEncryptedString:
    return DeterministicEncryptedString(key=_KEY, length=512)


def test_encrypt_decrypt_roundtrip(enc_type: DeterministicEncryptedString) -> None:
    """明文 → 加密 → 解密 → 拿回原本明文。"""
    original: str = "alice@example.com"

    ciphertext: str | None = enc_type.process_bind_param(original, dialect=None)
    assert ciphertext is not None
    assert ciphertext != original  # 存 DB 的是密文
    assert "@" not in ciphertext  # 不含明文特徵

    decrypted: str | None = enc_type.process_result_value(ciphertext, dialect=None)
    assert decrypted == original  # 解密還原


def test_deterministic_same_output(enc_type: DeterministicEncryptedString) -> None:
    """同 email 加密兩次應該得到相同密文（unique constraint 才 work）。"""
    email: str = "alice@example.com"

    enc1: str | None = enc_type.process_bind_param(email, dialect=None)
    enc2: str | None = enc_type.process_bind_param(email, dialect=None)

    assert enc1 == enc2  # 每次結果相同


def test_different_input_different_output(enc_type: DeterministicEncryptedString) -> None:
    """不同 email 加密結果應該不同。"""
    enc1: str | None = enc_type.process_bind_param("alice@example.com", dialect=None)
    enc2: str | None = enc_type.process_bind_param("bob@example.com", dialect=None)

    assert enc1 != enc2


def test_none_value_passthrough(enc_type: DeterministicEncryptedString) -> None:
    """None → None（NULL 欄位不加密）。"""
    assert enc_type.process_bind_param(None, dialect=None) is None
    assert enc_type.process_result_value(None, dialect=None) is None


def test_different_keys_produce_different_ciphertext() -> None:
    """不同 key 加密同 email 結果不同。"""
    type_a: DeterministicEncryptedString = DeterministicEncryptedString(
        key=b"key-a-32-chars-min-length-random"
    )
    type_b: DeterministicEncryptedString = DeterministicEncryptedString(
        key=b"key-b-32-chars-min-length-random"
    )

    enc_a: str | None = type_a.process_bind_param("alice@example.com", dialect=None)
    enc_b: str | None = type_b.process_bind_param("alice@example.com", dialect=None)

    assert enc_a != enc_b


def test_ciphertext_no_iv_prefix(enc_type: DeterministicEncryptedString) -> None:
    """驗證密文不含 IV prefix（固定 IV 設計，不需要存）。"""
    ciphertext: str | None = enc_type.process_bind_param("test@example.com", dialect=None)
    assert ciphertext is not None
    # AES 加密 17 bytes 明文 → padded 32 bytes → hex 64 chars（無 IV prefix）
    # 若含 IV 會是 96 chars（32 IV hex + 64 ciphertext hex）
    assert len(ciphertext) == 64


@pytest.mark.parametrize(
    "email",
    [
        "a@b.co",
        "alice@example.com",
        "first.last@subdomain.example.co.uk",
        "user+tag@example.com",
        "user_name@example.com",
        "a" * 64 + "@example.com",  # local part 上限
    ],
)
def test_roundtrip_various_emails(enc_type: DeterministicEncryptedString, email: str) -> None:
    """各種合法 email 格式 encrypt/decrypt round-trip 正確。"""
    ciphertext: str | None = enc_type.process_bind_param(email, dialect=None)
    decrypted: str | None = enc_type.process_result_value(ciphertext, dialect=None)
    assert decrypted == email
