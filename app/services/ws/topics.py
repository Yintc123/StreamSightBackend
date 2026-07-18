"""Topic 授權掛勾（websocket §2.9）。

連線本身只要 role=1 + active；個別 topic 可要求更高 admin_role。本模組只提供**掛勾點**
與預設（任一 admin 皆可訂閱）；**topic → 最低等級的對照表屬業務規格**，由各業務填充。
"""

from app.core.enums import AdminRole

# 業務 topic → 最低 admin_role 門檻。預設空 = 任一 admin 皆可訂閱（§2.9）。
# 業務規格於此註冊，例如：TOPIC_MIN_ROLE["monitor.secrets"] = AdminRole.SUPER_ADMIN
TOPIC_MIN_ROLE: dict[str, AdminRole] = {}


def topic_min_role(topic: str) -> AdminRole | None:
    """回該 topic 要求的最低 admin_role；None = 任一 admin 皆可訂閱。"""
    return TOPIC_MIN_ROLE.get(topic)
