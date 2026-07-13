"""Argon2id password hashing (offloaded to threadpool to avoid blocking event loop).

Argon2 hash / verify 是 CPU-bound (~50ms each with default params). 直接 sync 呼叫
會阻塞 asyncio event loop、所有其他 endpoint 都會被卡。用 `asyncio.to_thread` 把
sync C call 包成 awaitable、event loop 期間得以繼續處理其他 request。

雖然 argon2-cffi 底層 release GIL、但「主 thread 仍在 C code 中被占用」— GIL
的釋放只讓別的 thread 有機會跑、event loop 本身仍卡在主 thread 上。to_thread
才是把工作交給 worker thread、讓主 thread（event loop）真正空出來的做法。
"""

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_password_hasher: PasswordHasher = PasswordHasher()


async def hash_password(plain: str) -> str:
    """Hash a plaintext password using argon2id (offloaded to threadpool)."""
    return await asyncio.to_thread(_password_hasher.hash, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    """Verify plaintext against argon2 hash (offloaded to threadpool)."""

    def _verify() -> bool:
        try:
            _password_hasher.verify(hashed, plain)
            return True
        except VerifyMismatchError:
            return False

    return await asyncio.to_thread(_verify)
