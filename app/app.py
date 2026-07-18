import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from .api import api_router
from .api.middlewares import RequestIdMiddleware
from .core.config import BaseAppSettings, get_app_settings
from .core.db import AsyncSessionLocal, engine
from .core.exceptions import setup_exception_handlers
from .core.logging import setup_logging
from .core.redis import close_redis, redis_client
from .services.monitoring.db_probe import MariaDbStatsProbe, PoolStatsProbe
from .services.monitoring.log_handler import RedisStreamLogHandler, run_log_flusher
from .services.monitoring.sampler import MonitoringSampler
from .services.monitoring.store import RedisStreamStore
from .services.ws import ConnectionManager
from .services.ws.bridge import WsBridge
from .services.ws.protocol import WSCloseCode
from .services.ws.publisher import Publisher


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """App lifespan：startup / shutdown（websocket §2.10，monitoring.md §2.5）。"""
    settings: BaseAppSettings = get_app_settings()

    # startup：WS bridge
    bridge: WsBridge = WsBridge(redis_client, app.state.ws_manager)
    await bridge.start()
    app.state.ws_bridge = bridge

    # startup：Monitoring（monitoring_enabled 總開關）
    flush_task: asyncio.Task | None = None
    sampler: MonitoringSampler | None = None
    if settings.monitoring_enabled:
        import asyncio as _asyncio

        log_queue: asyncio.Queue[dict] = asyncio.Queue(
            maxsize=settings.monitoring_log_queue_maxsize
        )
        app.state.monitoring_log_queue = log_queue
        store = RedisStreamStore(redis_client)
        publisher = Publisher(redis_client)

        # 掛 log handler（僅非測試環境）
        if settings.app_env != "test":
            import logging as _logging

            handler = RedisStreamLogHandler(log_queue)
            _logging.getLogger().addHandler(handler)
            app.state.monitoring_log_handler = handler

        # 背景 flush task
        flush_task = _asyncio.create_task(
            run_log_flusher(
                log_queue,
                store,
                stream="monitor:stream:logs",
                maxlen=settings.monitoring_log_stream_maxlen,
                batch_size=settings.monitoring_log_flush_batch_size,
                interval=float(settings.monitoring_log_flush_interval_seconds),
            ),
            name="monitoring-log-flusher",
        )

        # 採樣器
        if settings.db_dialect.startswith("mysql"):
            probe = MariaDbStatsProbe(engine, AsyncSessionLocal, settings.db_name)
        else:
            probe = PoolStatsProbe(engine)

        sampler = MonitoringSampler(
            client=redis_client,
            probe=probe,
            store=store,
            publisher=publisher,
            stream="monitor:stream:db",
            maxlen=settings.monitoring_db_stream_maxlen,
            sample_interval=float(settings.monitoring_db_sample_interval_seconds),
            lease_seconds=settings.monitoring_sampler_leader_lease_seconds,
        )
        await sampler.start()
        app.state.monitoring_sampler = sampler

    yield

    # shutdown：monitoring
    if sampler:
        await sampler.stop()
    if flush_task and not flush_task.done():
        flush_task.cancel()
        with suppress(asyncio.CancelledError):
            await flush_task

    # shutdown：WS 優雅斷線 → bridge → DB/Redis
    await app.state.ws_manager.close_all(WSCloseCode.SERVICE_RESTART)
    await bridge.stop()
    await engine.dispose()
    await close_redis()


def create_app() -> FastAPI:
    setup_logging()
    app_settings: BaseAppSettings = get_app_settings()

    app: FastAPI = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        debug=app_settings.app_debug,
        lifespan=lifespan,
    )

    setup_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    # per-process WS 連線註冊表（app.state 單例；於 create_app 建立，不依賴 lifespan，
    # 讓測試的 ASGITransport（不跑 lifespan）也能取用，見 websocket §2.3/§4）。
    app.state.ws_manager = ConnectionManager()

    app.include_router(api_router)

    return app
