"""SQLAlchemy custom column types.

DeterministicEncryptedString:
    AES-256-CBC with fixed IV. Same plaintext → same ciphertext
    (index-friendly, unique constraint works), and reversible.

    Trade-offs:
      - Vulnerable to frequency analysis (acceptable for "hide from DB
        admin" use case; not for high-security data).
      - Once data is encrypted, the key MUST NEVER change (old data
        becomes unrecoverable).
"""

import hashlib
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy.types import String, TypeDecorator

# Fixed IV: acceptable for deterministic encryption on short strings (email).
# All-zero IV is fine here since we WANT deterministic output; randomness
# would defeat the purpose (breaks index / unique constraint).
_FIXED_IV: bytes = b"\x00" * 16


class DeterministicEncryptedString(TypeDecorator[str]):
    """AES-256-CBC + fixed IV column type.

    Usage:
        email: Mapped[str] = mapped_column(
            DeterministicEncryptedString(key=KEY, length=512),
            unique=True,
            index=True,
        )

    - `key` may be any bytes; SHA-256 normalizes to 32-byte AES-256 key.
    - `length` should accommodate max plaintext length × 2 (hex encoding)
      plus AES block padding.
    """

    impl = String
    cache_ok = True

    def __init__(self, key: bytes, length: int = 512) -> None:
        self._key: bytes = hashlib.sha256(key).digest()
        super().__init__(length=length)

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        """Called on INSERT/UPDATE: encrypt plaintext before storing."""
        if value is None:
            return None
        cipher: Cipher = Cipher(algorithms.AES(self._key), modes.CBC(_FIXED_IV))
        padder = padding.PKCS7(128).padder()
        padded: bytes = padder.update(value.encode()) + padder.finalize()
        encryptor = cipher.encryptor()
        ciphertext: bytes = encryptor.update(padded) + encryptor.finalize()
        return ciphertext.hex()

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        """Called on SELECT: decrypt stored ciphertext back to plaintext."""
        if value is None:
            return None
        ciphertext: bytes = bytes.fromhex(value)
        cipher: Cipher = Cipher(algorithms.AES(self._key), modes.CBC(_FIXED_IV))
        decryptor = cipher.decryptor()
        padded: bytes = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode()
