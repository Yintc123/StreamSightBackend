"""WebSocket 訊息封套 DTO（跨層，framework-agnostic）。見 websocket §3.2/§3.3。

所有訊息為 JSON 物件、以 `type` 辨識。送出一律 `model_dump(mode="json")`
（StrEnum type 值 → wire 字串，可直接 ws.send_json）。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.ws.protocol import WSMessageType


# ── server → client（推播/回應，§3.3）──────────────────────────────────────
class WelcomeMessage(BaseModel):
    """accept 後首則：告知連線 id 與自身等級。"""

    type: Literal[WSMessageType.WELCOME] = WSMessageType.WELCOME
    connection_id: str
    admin_role: int  # rank = value（IntEnum，方案 A：wire 也 int）；enum-int


class SubscriptionAck(BaseModel):
    """訂閱狀態回應（type 為 subscribed / unsubscribed）。"""

    type: Literal[WSMessageType.SUBSCRIBED, WSMessageType.UNSUBSCRIBED]
    topic: str


class EventMessage(BaseModel):
    """業務推播（server→client 主體；data 由各業務規格定義）。"""

    type: Literal[WSMessageType.EVENT] = WSMessageType.EVENT
    topic: str
    data: dict[str, Any]
    ts: int


class PingMessage(BaseModel):
    """心跳探測（client 須回 pong）。"""

    type: Literal[WSMessageType.PING] = WSMessageType.PING


class ErrorMessage(BaseModel):
    """非致命錯誤（訂閱越權、未知 type…）；連線續存。"""

    type: Literal[WSMessageType.ERROR] = WSMessageType.ERROR
    code: str
    message: str


# ── client → server（控制訊息，§3.2）──────────────────────────────────────
class ControlMessage(BaseModel):
    """控制訊息封套。type 限 client 值域（subscribe/unsubscribe/pong）；

    未知或 server 專屬 type → ValidationError（呼叫端據此回 error / close 4400）。
    topic 僅 subscribe/unsubscribe 需要（pong 無）。
    """

    type: Literal[WSMessageType.SUBSCRIBE, WSMessageType.UNSUBSCRIBE, WSMessageType.PONG]
    topic: str | None = Field(default=None)


__all__ = [
    "ControlMessage",
    "ErrorMessage",
    "EventMessage",
    "PingMessage",
    "SubscriptionAck",
    "WelcomeMessage",
]
