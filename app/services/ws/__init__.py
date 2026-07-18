"""WebSocket 基礎模組（Admin 即時推播）。見 docs/specs/websocket.md。"""

from .manager import Connection, ConnectionManager

__all__ = ["Connection", "ConnectionManager"]
