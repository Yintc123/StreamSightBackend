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

# 常數時間登入用的 dummy hash：帳號不存在時仍對它跑一次 argon2，拉平「存在 vs 不存在」
# 的回應時間、杜絕帳號列舉時序側通道（見 admin-account-refinement.md §5.4/§7）。
# ⚠️ module 頂層不能 await → 用同步的 _password_hasher.hash 算一次。
_DUMMY_PASSWORD_HASH: str = _password_hasher.hash("dummy-for-constant-time")


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


async def verify_password_or_dummy(stored_hash: str | None, plain: str) -> bool:
    """帳號存在→驗真 hash；不存在→驗 dummy（必 False），拉平時序、防列舉。

    共用 primitive（非塞進 admin_login），讓 user 端日後一行接上（見 §5.4）。
    """
    return await verify_password(
        plain, stored_hash if stored_hash is not None else _DUMMY_PASSWORD_HASH
    )
