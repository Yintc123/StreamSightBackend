"""WS 訊息封套 Pydantic 模型（websocket §3.2/§3.3）。

送出走 model_dump(mode="json")：StrEnum type 值序列化為 wire 字串、可直接 ws.send_json。
"""

import pytest
from pydantic import ValidationError

from app.dtos.ws import (
    ControlMessage,
    ErrorMessage,
    EventMessage,
    SubscriptionAck,
    WelcomeMessage,
)
from app.services.ws.protocol import WSMessageType


def test_welcome_wire_shape() -> None:
    msg = WelcomeMessage(connection_id="c-1", admin_role=50)
    assert msg.model_dump(mode="json") == {
        "type": "welcome",
        "connection_id": "c-1",
        "admin_role": 50,
    }


def test_event_wire_shape() -> None:
    msg = EventMessage(topic="monitor.jobs", data={"n": 1}, ts=1730000000)
    assert msg.model_dump(mode="json") == {
        "type": "event",
        "topic": "monitor.jobs",
        "data": {"n": 1},
        "ts": 1730000000,
    }


def test_subscription_ack_shape() -> None:
    sub = SubscriptionAck(type=WSMessageType.SUBSCRIBED, topic="t")
    assert sub.model_dump(mode="json") == {"type": "subscribed", "topic": "t"}


def test_error_wire_shape() -> None:
    msg = ErrorMessage(code="forbidden_topic", message="not allowed")
    assert msg.model_dump(mode="json") == {
        "type": "error",
        "code": "forbidden_topic",
        "message": "not allowed",
    }


def test_control_message_subscribe_parses() -> None:
    ctrl = ControlMessage.model_validate({"type": "subscribe", "topic": "monitor.jobs"})
    assert ctrl.type is WSMessageType.SUBSCRIBE
    assert ctrl.topic == "monitor.jobs"


def test_control_message_pong_has_no_topic() -> None:
    ctrl = ControlMessage.model_validate({"type": "pong"})
    assert ctrl.type is WSMessageType.PONG
    assert ctrl.topic is None


def test_control_message_rejects_server_only_type() -> None:
    """client 值域封閉：welcome 等 server 型別不可作控制訊息。"""
    with pytest.raises(ValidationError):
        ControlMessage.model_validate({"type": "welcome"})


def test_control_message_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        ControlMessage.model_validate({"type": "totally-unknown"})
