"""WS 端點 helper：cid 清洗（§2.12b）與 Origin 檢查（§6）。"""

from typing import cast

import pytest
from starlette.websockets import WebSocket

from app.api.routers.ws.router import _clean_cid, _origin_allowed
from app.core.config import get_app_settings


class _FakeWS:
    def __init__(self, origin: str | None) -> None:
        self.headers = {} if origin is None else {"origin": origin}


def _ws(origin: str | None) -> WebSocket:
    return cast(WebSocket, _FakeWS(origin))


def test_clean_cid_accepts_valid() -> None:
    assert _clean_cid("tab-1_ABC") == "tab-1_ABC"
    assert _clean_cid("0f8e2c1a") == "0f8e2c1a"


def test_clean_cid_rejects_empty_and_none() -> None:
    assert _clean_cid(None) is None
    assert _clean_cid("") is None


def test_clean_cid_rejects_non_ascii() -> None:
    """字元集限 ASCII [A-Za-z0-9_-]；Unicode 字母/全形數字非法（isalnum 會誤放）。"""
    assert _clean_cid("café") is None
    assert _clean_cid("Ⅳ") is None
    assert _clean_cid("１２３") is None


def test_clean_cid_rejects_injection_chars() -> None:
    assert _clean_cid("a:b") is None
    assert _clean_cid("a b") is None
    assert _clean_cid("a/b") is None


def test_clean_cid_rejects_too_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_app_settings(), "ws_cid_max_length", 8)
    assert _clean_cid("x" * 9) is None
    assert _clean_cid("x" * 8) == "x" * 8


def test_origin_allowed_empty_list_permits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_app_settings(), "ws_allowed_origins", [])
    assert _origin_allowed(_ws("https://anything")) is True
    assert _origin_allowed(_ws(None)) is True


def test_origin_allowed_enforces_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_app_settings(), "ws_allowed_origins", ["https://good"])
    assert _origin_allowed(_ws("https://good")) is True
    assert _origin_allowed(_ws("https://evil")) is False
    assert _origin_allowed(_ws(None)) is False
