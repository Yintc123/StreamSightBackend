"""WS 協定常數：關閉碼與訊息型別（websocket §3.2/§3.3/§3.4）。"""

from app.services.ws.protocol import (
    CLIENT_MESSAGE_TYPES,
    SERVER_MESSAGE_TYPES,
    WSCloseCode,
    WSMessageType,
)


def test_close_codes() -> None:
    assert WSCloseCode.UNAUTHENTICATED == 4401
    assert WSCloseCode.FORBIDDEN == 4403
    assert WSCloseCode.PROTOCOL_ERROR == 4400
    assert WSCloseCode.REPLACED == 4409
    assert WSCloseCode.HEARTBEAT_TIMEOUT == 4000
    assert WSCloseCode.BACKPRESSURE == 1013
    assert WSCloseCode.SERVICE_RESTART == 1012


def test_client_message_types_closed_domain() -> None:
    """client→server 值域封閉：只有 subscribe / unsubscribe / pong（§3.2）。"""
    assert {
        WSMessageType.SUBSCRIBE,
        WSMessageType.UNSUBSCRIBE,
        WSMessageType.PONG,
    } == CLIENT_MESSAGE_TYPES


def test_server_message_types() -> None:
    """server→client：welcome/subscribed/unsubscribed/event/ping/error（§3.3）。"""
    assert {
        WSMessageType.WELCOME,
        WSMessageType.SUBSCRIBED,
        WSMessageType.UNSUBSCRIBED,
        WSMessageType.EVENT,
        WSMessageType.PING,
        WSMessageType.ERROR,
    } == SERVER_MESSAGE_TYPES


def test_message_type_values_are_wire_strings() -> None:
    assert WSMessageType.SUBSCRIBE == "subscribe"
    assert WSMessageType.EVENT == "event"
    assert WSMessageType.PING == "ping"
    assert WSMessageType.ERROR == "error"
