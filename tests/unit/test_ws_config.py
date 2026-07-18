"""WS 設定預設值（websocket §4.1）。所有 ws_* 走 config、時間單位一律「秒」。"""

from app.core.config import BaseAppSettings, get_app_settings


def test_ws_config_defaults() -> None:
    settings: BaseAppSettings = get_app_settings()

    assert settings.ws_ticket_ttl_seconds == 180
    assert settings.ws_ping_interval_seconds == 30
    assert settings.ws_missed_pong_limit == 2
    assert settings.ws_idle_timeout_seconds == 120
    assert settings.ws_max_send_queue == 100
    assert settings.ws_reauth_interval_seconds == 300
    assert settings.ws_max_connections_per_principal == 10
    assert settings.ws_max_connections_total == 10000
    assert settings.ws_max_message_bytes == 16384
    assert settings.ws_control_msg_rate_limit == 20
    assert settings.ws_cid_max_length == 64
    assert settings.ws_allowed_origins == []
