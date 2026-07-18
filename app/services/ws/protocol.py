"""WS 封套型別與關閉碼常數（websocket §3.2/§3.3/§3.4）。

訊息協定：所有訊息為 JSON 物件、以 `type` 欄位辨識。控制訊息（client→server）與
推播（server→client）共用封套形狀，但 `type` 值域不同（見兩個 frozenset）。
"""

from enum import IntEnum, StrEnum


class WSCloseCode(IntEnum):
    """WS 關閉碼（§3.4）。4xxx 為 application-defined（RFC 6455 4000–4999）；1012/1013 標準碼。"""

    HEARTBEAT_TIMEOUT = 4000  # 連續未回 pong（§2.7）
    PROTOCOL_ERROR = 4400  # 非 JSON／超大訊息／格式錯（§2.6）
    UNAUTHENTICATED = 4401  # ticket 無效/過期/已用過、非 admin、inactive、被 kick、複查失效
    FORBIDDEN = 4403  # （保留）連線層等級不足
    REPLACED = 4409  # 同一分頁（sid+cid）開新連線取代舊連線（§2.12b）；client 收到不重連
    BACKPRESSURE = 1013  # 背壓斷線（佇列滿，§2.8）／資源上限過載
    SERVICE_RESTART = 1012  # 實例關閉（lifespan shutdown 優雅斷線）


class WSMessageType(StrEnum):
    """訊息 `type` 值（wire string）。"""

    # client → server（控制訊息，§3.2）
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    PONG = "pong"
    # server → client（推播/回應，§3.3）
    WELCOME = "welcome"
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    EVENT = "event"
    PING = "ping"
    ERROR = "error"


# client→server 值域封閉（未知 type → error，不關閉連線，§3.2）
CLIENT_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset(
    {WSMessageType.SUBSCRIBE, WSMessageType.UNSUBSCRIBE, WSMessageType.PONG}
)
# server→client 值域
SERVER_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset(
    {
        WSMessageType.WELCOME,
        WSMessageType.SUBSCRIBED,
        WSMessageType.UNSUBSCRIBED,
        WSMessageType.EVENT,
        WSMessageType.PING,
        WSMessageType.ERROR,
    }
)


# ── Redis pub/sub channel（跨實例 fan-out，§2.4/§2.5）──────────────────────
CHANNEL_BROADCAST: str = "ws:broadcast"

# 各實例 bridge psubscribe 的 pattern（涵蓋推播 + 兩種粒度的 kick）
PSUBSCRIBE_PATTERNS: tuple[str, ...] = (
    "ws:principal:*",
    "ws:topic:*",
    CHANNEL_BROADCAST,
    "ws:disconnect:principal:*",
    "ws:disconnect:sid:*",
)


def channel_principal(principal_id: int) -> str:
    return f"ws:principal:{principal_id}"


def channel_topic(topic: str) -> str:
    return f"ws:topic:{topic}"


def channel_disconnect_principal(principal_id: int) -> str:
    return f"ws:disconnect:principal:{principal_id}"


def channel_disconnect_sid(family_id: str) -> str:
    return f"ws:disconnect:sid:{family_id}"


__all__ = [
    "CHANNEL_BROADCAST",
    "CLIENT_MESSAGE_TYPES",
    "PSUBSCRIBE_PATTERNS",
    "SERVER_MESSAGE_TYPES",
    "WSCloseCode",
    "WSMessageType",
    "channel_disconnect_principal",
    "channel_disconnect_sid",
    "channel_principal",
    "channel_topic",
]
