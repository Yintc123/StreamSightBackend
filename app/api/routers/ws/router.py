"""WebSocket 端點（websocket §2.1/§3.1）。

- POST /ws/ticket：HTTP + JWT 認證（get_current_admin）→ 換一張短命單次 ticket。
- GET  /ws：WebSocket upgrade，帶 ticket；accept 前 GETDEL 驗票 → 重載 Admin 讀現值
  → accept + welcome；失敗一律 accept 後 close(4401)（4xxx close code 只在 upgrade 成功後有效）。

分層：本層只做握手認證與委派；連線註冊/投遞在 services/ws。長連線的 DB 存取走
get_session_factory（每個工作單元短命 session、用畢即還，§2.2/§4），不用 Depends(get_session)。
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import (
    get_current_admin,
    get_current_token_sid,
    get_session_factory,
    get_ticket_service,
    get_ws_reauth_service,
)
from app.core.config import get_app_settings
from app.core.enums import ADMIN_ROLE_RANK, AdminRole
from app.dtos.ws import (
    ControlMessage,
    ErrorMessage,
    PingMessage,
    SubscriptionAck,
    WelcomeMessage,
)
from app.models import Admin
from app.repositories.admin import AdminRepository
from app.services.initial_admin import (
    INITIAL_ADMIN_PRINCIPAL_ID,
    build_initial_admin,
    initial_admin_enabled,
)
from app.services.ws.manager import Connection, ConnectionManager
from app.services.ws.protocol import WSCloseCode, WSMessageType
from app.services.ws.reauth import WsReauthService
from app.services.ws.ticket import TicketService
from app.services.ws.topics import topic_min_role

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/ws", tags=["ws"])


class WsTicketResponse(BaseModel):
    """短命單次 ticket，供 client 建立 WS 連線（§2.1）。"""

    ticket: str = Field(description="Opaque single-use ticket（放進 WS URL ?ticket=）")
    expires_in: int = Field(description="ticket 有效秒數（換票→開連線的寬限窗，非連線時長）")


@router.post("/ticket", response_model=WsTicketResponse)
async def issue_ws_ticket(
    admin: Admin = Depends(get_current_admin),
    sid: str | None = Depends(get_current_token_sid),
    tickets: TicketService = Depends(get_ticket_service),
) -> WsTicketResponse:
    """已認證 admin 換一張短命單次 ticket；綁 principal_id + 當次 access token 的 sid。"""
    ticket, ttl = await tickets.issue(admin.principal_id, sid)
    return WsTicketResponse(ticket=ticket, expires_in=ttl)


async def _load_active_admin(
    session_factory: async_sessionmaker[AsyncSession], principal_id: int
) -> Admin | None:
    """重載 Admin 讀現值（is_active + admin_role），開短命 session、用畢即還（§2.2）。

    principal_id == 0（初始 admin）：合成 super_admin、不查 DB（config 停用則回 None）。
    一般 admin：查 DB，inactive／不存在 → None。
    """
    if principal_id == INITIAL_ADMIN_PRINCIPAL_ID:
        return build_initial_admin() if initial_admin_enabled() else None
    async with session_factory() as session:
        admin: Admin | None = await AdminRepository(session).get_by_principal_id(principal_id)
    return admin if (admin is not None and admin.is_active) else None


_RATE_WINDOW_SECONDS: float = 10.0  # 控制訊息速率滑動窗（§4.1：則/10s）
# cid 合法字元集（§2.12b）：**ASCII** [A-Za-z0-9_-]。不可用 str.isalnum()（Unicode-aware，
# 會誤放重音字母/全形數字）。
_CID_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]+$")


def _origin_allowed(websocket: WebSocket) -> bool:
    """handshake Origin 檢查（防 CSWSH，§6）。空清單 = 不設限（dev/test；prod 應配置，§4.1）。"""
    allowed: list[str] = get_app_settings().ws_allowed_origins
    if not allowed:
        return True
    return websocket.headers.get("origin") in allowed


def _clean_cid(raw: str | None) -> str | None:
    """清洗 client 帶的 cid（§2.12b）：限長度 + 字元集 [A-Za-z0-9_-]；非法 → None（不參與 same-tab）。"""
    if not raw:
        return None
    max_len: int = get_app_settings().ws_cid_max_length
    if len(raw) > max_len or not _CID_RE.fullmatch(raw):
        return None
    return raw


async def _handle_control(manager: ConnectionManager, conn: Connection, raw: object) -> None:
    """處理一則控制訊息（subscribe/unsubscribe/pong）。未知/格式錯 → error（不關閉，§3.2）。"""
    try:
        msg: ControlMessage = ControlMessage.model_validate(raw)
    except ValidationError:
        await manager.send_to_connection(
            conn,
            ErrorMessage(code="invalid_message", message="unknown or malformed message").model_dump(
                mode="json"
            ),
        )
        return

    if msg.type is WSMessageType.PONG:
        conn.missed_pongs = 0  # 心跳存活：收到 pong → 重置未回計數（§2.7）
        return

    if msg.topic is None:
        await manager.send_to_connection(
            conn,
            ErrorMessage(code="missing_topic", message="topic required").model_dump(mode="json"),
        )
        return

    if msg.type is WSMessageType.SUBSCRIBE:
        min_role: AdminRole | None = topic_min_role(msg.topic)
        if min_role is not None and (
            ADMIN_ROLE_RANK[AdminRole(conn.admin_role)] < ADMIN_ROLE_RANK[min_role]
        ):
            await manager.send_to_connection(
                conn,
                ErrorMessage(
                    code="forbidden_topic", message="insufficient admin role for topic"
                ).model_dump(mode="json"),
            )
            return
        await manager.subscribe(conn, msg.topic)
        await manager.send_to_connection(
            conn,
            SubscriptionAck(type=WSMessageType.SUBSCRIBED, topic=msg.topic).model_dump(mode="json"),
        )
    elif msg.type is WSMessageType.UNSUBSCRIBE:
        await manager.unsubscribe(conn, msg.topic)
        await manager.send_to_connection(
            conn,
            SubscriptionAck(type=WSMessageType.UNSUBSCRIBED, topic=msg.topic).model_dump(
                mode="json"
            ),
        )


async def _run_heartbeat(manager: ConnectionManager, conn: Connection) -> None:
    """心跳與閒置逾時（§2.7）：每 ping_interval 送 ping；連續 missed_pong_limit 次未回，
    或距上次進站訊息（含 pong）超過 idle_timeout → close(4000)。"""
    settings = get_app_settings()
    interval: float = settings.ws_ping_interval_seconds
    limit: int = settings.ws_missed_pong_limit
    idle_timeout: float = settings.ws_idle_timeout_seconds
    ping: dict = PingMessage().model_dump(mode="json")
    while not conn.closed:
        await asyncio.sleep(interval)
        if conn.closed:
            return
        # 閒置逾時：無任何進站訊息（含 pong）超過 idle_timeout → 判死（liveness timeout, 4000）
        if time.monotonic() - conn.last_seen > idle_timeout:
            await conn.close(WSCloseCode.HEARTBEAT_TIMEOUT)
            await manager._teardown(conn)
            return
        if conn.missed_pongs >= limit:
            await conn.close(WSCloseCode.HEARTBEAT_TIMEOUT)
            await manager._teardown(conn)
            return
        conn.missed_pongs += 1
        await manager.send_to_connection(conn, ping)


async def _run_reauth(
    manager: ConnectionManager, conn: Connection, reauth: WsReauthService
) -> None:
    """每 reauth_interval 複查 is_active + session 有效性；失效 → close(4401)（§2.2）。"""
    interval: float = get_app_settings().ws_reauth_interval_seconds
    while not conn.closed:
        await asyncio.sleep(interval)
        if conn.closed:
            return
        valid: bool = await reauth.is_connection_valid(
            principal_id=conn.principal_id, sid=conn.sid, now=datetime.now(UTC)
        )
        if not valid:
            await conn.close(WSCloseCode.UNAUTHENTICATED)
            await manager._teardown(conn)
            return


@router.websocket("")
async def admin_ws(
    websocket: WebSocket,
    tickets: TicketService = Depends(get_ticket_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    reauth: WsReauthService = Depends(get_ws_reauth_service),
) -> None:
    """WS handshake：Origin → 驗票 → 重載 Admin → 連線上限 → accept + welcome → 控制訊息迴圈。"""
    settings = get_app_settings()

    # Origin 檢查在最前（handshake 階段拒絕、不 accept、不消耗 ticket，§6/§4.1）。
    if not _origin_allowed(websocket):
        await websocket.close()  # 未 accept → 握手層失敗（非 4xxx close frame）
        return

    ticket: str | None = websocket.query_params.get("ticket")
    consumed = await tickets.consume(ticket) if ticket else None
    if consumed is None:
        # 無票／壞票／已用過 → accept 後 close 4401（讓 client 讀得到 4xxx code）
        await websocket.accept()
        await websocket.close(code=WSCloseCode.UNAUTHENTICATED)
        return

    principal_id, sid = consumed
    admin: Admin | None = await _load_active_admin(session_factory, principal_id)
    if admin is None:
        await websocket.accept()
        await websocket.close(code=WSCloseCode.UNAUTHENTICATED)
        return

    await websocket.accept()
    manager: ConnectionManager = websocket.app.state.ws_manager
    cid: str | None = _clean_cid(websocket.query_params.get("cid"))

    # 資源上限（防 DoS，§6）：per-principal／全實例連線數。同分頁重連（同 sid+cid）豁免
    # （它會取代舊連線、非淨增）。超限採 1013（過載語意）。
    if not manager.has_sid_cid(sid, cid) and (
        manager.total_connections >= settings.ws_max_connections_total
        or len(manager.connections_for_principal(principal_id))
        >= settings.ws_max_connections_per_principal
    ):
        await websocket.close(code=WSCloseCode.BACKPRESSURE)
        return

    conn: Connection = Connection(
        ws=websocket,
        principal_id=principal_id,
        admin_role=admin.admin_role,
        sid=sid,
        cid=cid,
        is_active=True,
        queue=asyncio.Queue(maxsize=settings.ws_max_send_queue),
    )
    # welcome 為 accept 後**首則**（§3.3）：在 register 啟動 writer 前先入列，保證它先於任何
    # 之後可能的推播（如剛連上就被 to_principal 命中）送出。
    conn.queue.put_nowait(
        WelcomeMessage(connection_id=conn.connection_id, admin_role=admin.admin_role).model_dump(
            mode="json"
        )
    )
    await manager.register(
        conn
    )  # 啟動 writer（送出已入列的 welcome）；同 (sid,cid) 舊連線 → close(4409) 取代
    # 背景 task：心跳判死（4000）＋ 定期複查失效（4401）。teardown 時一併取消（§2.2/§2.7）。
    conn.heartbeat_task = asyncio.create_task(_run_heartbeat(manager, conn))
    conn.reauth_task = asyncio.create_task(_run_reauth(manager, conn, reauth))

    # 控制訊息迴圈：僅收 subscribe/unsubscribe/pong；斷線即 teardown（reader 側偵測）。
    max_bytes: int = settings.ws_max_message_bytes
    rate_limit: int = settings.ws_control_msg_rate_limit
    recent: deque[float] = deque()  # 控制訊息時間戳（每連線滑動窗速率限制，§6）
    try:
        while True:
            text: str = await websocket.receive_text()
            conn.last_seen = time.monotonic()  # 任何進站訊息都算活動（重置閒置計時，§2.7）
            # 超大訊息（防記憶體濫用）→ close 4400（§3.4）
            if len(text.encode("utf-8")) > max_bytes:
                await conn.close(WSCloseCode.PROTOCOL_ERROR)
                break
            # 速率限制（每連線滑動窗）：超過 → error（不斷線，§4.1）
            now: float = time.monotonic()
            while recent and now - recent[0] > _RATE_WINDOW_SECONDS:
                recent.popleft()
            if len(recent) >= rate_limit:
                await manager.send_to_connection(
                    conn,
                    ErrorMessage(
                        code="rate_limited", message="too many control messages"
                    ).model_dump(mode="json"),
                )
                continue
            recent.append(now)
            # 非 JSON／格式錯 → close 4400（§3.4）
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                await conn.close(WSCloseCode.PROTOCOL_ERROR)
                break
            await _handle_control(manager, conn, raw)
    except WebSocketDisconnect:
        pass
    finally:
        await manager._teardown(conn)
