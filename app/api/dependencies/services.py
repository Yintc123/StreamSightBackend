import redis.asyncio as redis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import AdminService, AuthService, UserService
from app.services.ws.publisher import Publisher
from app.services.ws.reauth import WsReauthService
from app.services.ws.ticket import TicketService

from .db import get_session, get_session_factory
from .redis import get_redis


def get_ticket_service(
    client: redis.Redis = Depends(get_redis),
) -> TicketService:
    """FastAPI dependency: build a TicketService bound to the shared Redis client（WS §2.1）。"""
    return TicketService(client)


def get_ws_publisher(
    client: redis.Redis = Depends(get_redis),
) -> Publisher:
    """FastAPI dependency: build a WS Publisher（推播/kick → Redis pub/sub，§2.4/§2.5）。"""
    return Publisher(client)


def get_ws_reauth_service(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> WsReauthService:
    """FastAPI dependency：持 session 工廠的 WsReauthService（WS 定期複查，§2.2/§4）。

    比照 get_*_service 形狀。WS 端點於 accept 時捕獲一次交給背景複查 task 反覆用
    （背景 task 在 request scope 外，Depends 只解析一次）。
    """
    return WsReauthService(session_factory)


def get_admin_service(
    session: AsyncSession = Depends(get_session),
    publisher: Publisher = Depends(get_ws_publisher),
) -> AdminService:
    """FastAPI dependency: build an AdminService bound to the request's session.

    帶 WS Publisher：archive/delete/change_password 撤 token 後即時斷該 admin 的 WS（§2.5）。
    """
    return AdminService(session, publisher)


def get_user_service(
    session: AsyncSession = Depends(get_session),
) -> UserService:
    """FastAPI dependency: build a UserService bound to the request's session."""
    return UserService(session)


def get_auth_service(
    session: AsyncSession = Depends(get_session),
    publisher: Publisher = Depends(get_ws_publisher),
) -> AuthService:
    """FastAPI dependency: build an AuthService bound to the request's session.

    帶 WS Publisher：logout 斷該 session、logout_all 斷該 principal 的 WS（§2.5）。
    """
    return AuthService(session, publisher)
