import redis.asyncio as redis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_app_settings
from app.services import AdminService, AuthService, RecordService, UserService
from app.services.monitoring.db_probe import MariaDbStatsProbe, PoolStatsProbe
from app.services.monitoring.db_stats import DbStatsService
from app.services.monitoring.logs import LogQueryService
from app.services.monitoring.metrics import MetricQueryService
from app.services.monitoring.store import RedisStreamStore, TimeSeriesStore
from app.services.realtime.history import RealtimeHistoryService
from app.services.ws.publisher import Publisher
from app.services.ws.reauth import WsReauthService
from app.services.ws.ticket import TicketService

from .db import get_engine, get_session, get_session_factory
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


def get_record_service(
    session: AsyncSession = Depends(get_session),
) -> RecordService:
    """FastAPI dependency: build a RecordService bound to the request's session（records §2.6）。"""
    return RecordService(session)


def get_auth_service(
    session: AsyncSession = Depends(get_session),
    publisher: Publisher = Depends(get_ws_publisher),
) -> AuthService:
    """FastAPI dependency: build an AuthService bound to the request's session.

    帶 WS Publisher：logout 斷該 session、logout_all 斷該 principal 的 WS（§2.5）。
    """
    return AuthService(session, publisher)


# ── Monitoring dependencies（monitoring.md §4）───────────────────────────────


def get_time_series_store(
    client: redis.Redis = Depends(get_redis),
) -> TimeSeriesStore:
    """FastAPI dependency: Redis Stream 實作的 TimeSeriesStore。"""
    return RedisStreamStore(client)


def get_log_query_service(
    store: TimeSeriesStore = Depends(get_time_series_store),
) -> LogQueryService:
    return LogQueryService(store)


def get_db_stats_service(
    engine: AsyncEngine = Depends(get_engine),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> DbStatsService:
    """依 db_dialect 自動選用 probe（monitoring.md §2.4）。"""
    settings = get_app_settings()
    if settings.db_dialect.startswith("mysql"):
        probe = MariaDbStatsProbe(engine, session_factory, settings.db_name)
    else:
        probe = PoolStatsProbe(engine)
    return DbStatsService(probe)


def get_metric_query_service(
    store: TimeSeriesStore = Depends(get_time_series_store),
) -> MetricQueryService:
    return MetricQueryService(store)


def get_realtime_history_service(
    session: AsyncSession = Depends(get_session),
) -> RealtimeHistoryService:
    """FastAPI dependency: build a RealtimeHistoryService（realtime-history.md §3.2）。"""
    return RealtimeHistoryService(session)
