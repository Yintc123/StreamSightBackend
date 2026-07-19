"""Topic 授權掛勾（websocket §2.9）。

連線本身只要 role=1 + active；個別 topic 可要求更高 admin_role。本模組只提供**掛勾點**
與預設（任一 admin 皆可訂閱）；**topic → 最低等級的對照表屬業務規格**，由各業務填充。
"""

from app.core.enums import AdminRole

# 業務 topic → 最低 admin_role 門檻。預設空 = 任一 admin 皆可訂閱（§2.9）。
# monitoring.md §2.6：日誌含敏感上下文 → SUPER_ADMIN；DB 狀態 → VIEWER（任一 admin）。
TOPIC_MIN_ROLE: dict[str, AdminRole] = {
    "monitor.logs": AdminRole.SUPER_ADMIN,
    "monitor.db": AdminRole.VIEWER,
    "realtime.stream": AdminRole.VIEWER,  # realtime-stream.md §2.1
}


def topic_min_role(topic: str) -> AdminRole | None:
    """回該 topic 要求的最低 admin_role；None = 任一 admin 皆可訂閱。"""
    return TOPIC_MIN_ROLE.get(topic)
