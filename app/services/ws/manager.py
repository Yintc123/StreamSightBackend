"""ConnectionManager — per-instance 記憶體連線註冊/投遞/清理（websocket §2.3/§2.8/§2.12）。

職責：register（含同分頁取代）/unregister、subscribe/unsubscribe、對本實例連線投遞、
_teardown 清理死連線。**不含業務邏輯**（推什麼由 Publisher/呼叫端決定）。
每連線一個有界送出佇列 + 背景 writer task；慢消費者（佇列滿）即斷（close 1013）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from uuid import uuid4

from starlette.websockets import WebSocket, WebSocketDisconnect

from app.services.ws.protocol import WSCloseCode

logger: logging.Logger = logging.getLogger(__name__)

# transport 死亡的確定訊號（§2.12a）：send 丟這些 → 判定連線已死 → 立即 teardown。
# 只認斷線例外；序列化錯（TypeError 等）是 bug、只 log、不可斷連線（不要 except Exception）。
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    WebSocketDisconnect,
    ConnectionError,
    OSError,
    RuntimeError,  # 對已 close 的 socket send → Starlette 丟 RuntimeError
)


class Connection:
    """一條 WS 連線 + accept 當下快照的原生值（不掛 ORM，授權現值靠複查重讀，§4）。"""

    def __init__(
        self,
        *,
        ws: WebSocket,
        principal_id: int,
        admin_role: str,
        sid: str | None,
        cid: str | None,
        is_active: bool,
        queue: asyncio.Queue[dict],
        connection_id: str | None = None,
    ) -> None:
        self.ws: WebSocket = ws
        self.principal_id: int = principal_id
        self.admin_role: str = admin_role
        self.sid: str | None = sid
        self.cid: str | None = cid
        self.is_active: bool = is_active
        self.queue: asyncio.Queue[dict] = queue
        self.connection_id: str = connection_id or str(uuid4())
        self.subscriptions: set[str] = set()
        self.closed: bool = False
        self.missed_pongs: int = 0  # 連續未回 pong 計數（心跳判死，§2.7）
        self.last_seen: float = time.monotonic()  # 上次進站訊息時刻（閒置逾時，§2.7）
        self.writer_task: asyncio.Task[None] | None = None
        self.heartbeat_task: asyncio.Task[None] | None = None
        self.reauth_task: asyncio.Task[None] | None = None

    async def close(self, code: int = 1000) -> None:
        """best-effort 關閉底層 socket（socket 已死多為 no-op，吞例外）。"""
        with contextlib.suppress(Exception):
            await self.ws.close(code=code)


class ConnectionManager:
    """per-process 單例（掛 app.state）。管理本實例所有連線的註冊與投遞。"""

    def __init__(self) -> None:
        self._by_principal: dict[int, set[Connection]] = defaultdict(set)
        self._by_topic: dict[str, set[Connection]] = defaultdict(set)
        self._by_sid: dict[str, set[Connection]] = defaultdict(set)
        self._by_sid_cid: dict[tuple[str, str], Connection] = {}

    # ── 查詢（唯讀快照）───────────────────────────────────────
    def connections_for_principal(self, principal_id: int) -> set[Connection]:
        return set(self._by_principal.get(principal_id, set()))

    def connections_for_sid(self, sid: str) -> set[Connection]:
        return set(self._by_sid.get(sid, set()))

    def has_sid_cid(self, sid: str | None, cid: str | None) -> bool:
        """是否已有同 (sid, cid) 連線（供連線上限豁免同分頁重連，§2.12b）。"""
        if sid is None or cid is None:
            return False
        return (sid, cid) in self._by_sid_cid

    @property
    def total_connections(self) -> int:
        return sum(len(conns) for conns in self._by_principal.values())

    # ── 註冊 / 反註冊 ─────────────────────────────────────────
    async def register(self, conn: Connection, *, start_writer: bool = True) -> None:
        """註冊；若已有同 (sid, cid) 舊連線 → 先 close(4409)+_teardown 取代（§2.12b）。"""
        if conn.sid is not None and conn.cid is not None:
            key: tuple[str, str] = (conn.sid, conn.cid)
            existing: Connection | None = self._by_sid_cid.get(key)
            if existing is not None and existing is not conn:
                await existing.close(WSCloseCode.REPLACED)
                await self._teardown(existing)
            self._by_sid_cid[key] = conn

        self._by_principal[conn.principal_id].add(conn)
        if conn.sid is not None:
            self._by_sid[conn.sid].add(conn)

        if start_writer and conn.writer_task is None:
            conn.writer_task = asyncio.create_task(self._run_writer(conn))

    async def unregister(self, conn: Connection) -> None:
        """從四索引移除（principal / topic / sid / (sid,cid)）。"""
        self._by_principal.get(conn.principal_id, set()).discard(conn)
        if not self._by_principal.get(conn.principal_id):
            self._by_principal.pop(conn.principal_id, None)

        for topic in list(conn.subscriptions):
            self._by_topic.get(topic, set()).discard(conn)
            if not self._by_topic.get(topic):
                self._by_topic.pop(topic, None)

        if conn.sid is not None:
            self._by_sid.get(conn.sid, set()).discard(conn)
            if not self._by_sid.get(conn.sid):
                self._by_sid.pop(conn.sid, None)
            if conn.cid is not None and self._by_sid_cid.get((conn.sid, conn.cid)) is conn:
                self._by_sid_cid.pop((conn.sid, conn.cid), None)

    async def _teardown(self, conn: Connection) -> None:
        """冪等清理（只跑一次）：unregister + best-effort close + 取消 writer/heartbeat（§2.12a）。"""
        if conn.closed:
            return
        conn.closed = True
        await self.unregister(conn)
        # 真正的關閉碼一律在 teardown 前顯式送出（4409/1013/4401…）；此處僅確保 socket 關閉。
        await conn.close()
        for task in (conn.writer_task, conn.heartbeat_task, conn.reauth_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()

    # ── 訂閱 ──────────────────────────────────────────────────
    async def subscribe(self, conn: Connection, topic: str) -> None:
        conn.subscriptions.add(topic)
        self._by_topic[topic].add(conn)

    async def unsubscribe(self, conn: Connection, topic: str) -> None:
        conn.subscriptions.discard(topic)
        self._by_topic.get(topic, set()).discard(conn)
        if not self._by_topic.get(topic):
            self._by_topic.pop(topic, None)

    # ── 投遞 ──────────────────────────────────────────────────
    async def send_local(
        self,
        *,
        principal_id: int | None = None,
        topic: str | None = None,
        message: dict,
    ) -> int:
        """投遞給本實例符合條件的連線（principal 或 topic 擇一）；回投遞數。

        有界佇列：塞不進（慢消費者）→ close(1013)+_teardown。
        """
        if principal_id is not None:
            targets: set[Connection] = self.connections_for_principal(principal_id)
        elif topic is not None:
            targets = set(self._by_topic.get(topic, set()))
        else:
            return 0

        delivered: int = 0
        overflow: list[Connection] = []
        for conn in targets:
            if conn.closed:
                continue
            try:
                conn.queue.put_nowait(message)
                delivered += 1
            except asyncio.QueueFull:
                overflow.append(conn)

        for conn in overflow:
            await conn.close(WSCloseCode.BACKPRESSURE)
            await self._teardown(conn)

        return delivered

    async def send_to_connection(self, conn: Connection, message: dict) -> bool:
        """投遞給單一連線（welcome/ack/error 用）。佇列滿 → close(1013)+teardown、回 False。

        所有 server→client 送出都經此進佇列、由 writer 單一送出（避免併發 send）。
        """
        if conn.closed:
            return False
        try:
            conn.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            await conn.close(WSCloseCode.BACKPRESSURE)
            await self._teardown(conn)
            return False

    async def broadcast_local(self, message: dict) -> int:
        """投遞給本實例**所有**連線（Publisher.broadcast，§2.4）；回投遞數。"""
        delivered: int = 0
        overflow: list[Connection] = []
        for conns in list(self._by_principal.values()):
            for conn in list(conns):
                if conn.closed:
                    continue
                try:
                    conn.queue.put_nowait(message)
                    delivered += 1
                except asyncio.QueueFull:
                    overflow.append(conn)
        for conn in overflow:
            await conn.close(WSCloseCode.BACKPRESSURE)
            await self._teardown(conn)
        return delivered

    async def kick_local(self, principal_id: int, code: int = WSCloseCode.UNAUTHENTICATED) -> None:
        """關閉該 principal 的全部本地連線（撤權/登出，§2.5）。"""
        for conn in self.connections_for_principal(principal_id):
            await conn.close(code)
            await self._teardown(conn)

    async def kick_local_sid(self, sid: str, code: int = WSCloseCode.UNAUTHENTICATED) -> None:
        """只關該 session（sid）的本地連線（單一 logout，§2.5）。"""
        for conn in self.connections_for_sid(sid):
            await conn.close(code)
            await self._teardown(conn)

    async def close_all(self, code: int = WSCloseCode.SERVICE_RESTART) -> None:
        """關閉本實例**全部**連線並清理（lifespan shutdown 優雅斷線，§2.2/§3.4）。"""
        for conns in list(self._by_principal.values()):
            for conn in list(conns):
                await conn.close(code)
                await self._teardown(conn)

    # ── writer task ──────────────────────────────────────────
    async def _run_writer(self, conn: Connection) -> None:
        """逐一從有界佇列取出送給 client；transport 死亡即 teardown（§2.12a）。"""
        while True:
            message: dict = await conn.queue.get()
            try:
                await conn.ws.send_json(message)
            except _TRANSPORT_ERRORS:
                conn.queue.task_done()
                await self._teardown(conn)
                return
            except Exception:
                # 序列化錯等 bug：只 log、不斷連線（不要 except Exception 斷連）。
                logger.exception("WS send serialization error cid=%s", conn.cid)
            conn.queue.task_done()
