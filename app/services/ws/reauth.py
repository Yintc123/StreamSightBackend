"""WsReauthService — 長連線的 DB 存取（websocket §2.2/§4）。

持 session 工廠；每次呼叫開**短命 session** 讀現值（is_active + session 有效性），用畢即還。
不用 request-scoped session（避免綁死無上限連線壽命 + 並發共用單一 AsyncSession）。
授權現值一律靠複查重讀，不盲信 accept 當下快照。
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.repo_admin import AdminRepository
from app.repositories.repo_refresh_token import RefreshTokenRepository

# 「開一個 async session 的工廠」：呼叫回一個 yield AsyncSession 的 async context manager。
# 同時容納生產的 async_sessionmaker（`AsyncSessionLocal()` → AsyncSession，本身即 async CM）
# 與測試的 asynccontextmanager 工廠（共用 db_session）。見 §2.2/§4 與 conftest。
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class WsReauthService:
    """定期複查一條 WS 連線是否仍有效（否則呼叫端 close 4401）。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def is_connection_valid(
        self, *, principal_id: int, sid: str | None, now: datetime
    ) -> bool:
        """兩條件皆通過才回 True（is_active + session 有效性，§2.2）。

        bootstrap root 為真實 DB admin → 一律查 DB（is_active + session），無哨兵特判。
        Admin.is_active 為 False → False；sid 非 None 且該 family 已無 live token → False。
        """
        async with self._session_factory() as session:
            admin = await AdminRepository(session).get_by_principal_id(principal_id)
            if admin is None or not admin.is_active:
                return False
            if sid is not None:
                has_live = await RefreshTokenRepository(session).has_live_tokens_in_family(
                    sid, now=now
                )
                if not has_live:
                    return False
        return True
