"""Unit tests for app/core/enums AdminRole（權限等級 StrEnum）。§3.1/§8.1。"""

from app.core.enums import AdminRole


def test_admin_role_string_values() -> None:
    assert AdminRole.SUPER_ADMIN.value == "super_admin"
    assert AdminRole.EDITOR.value == "editor"
    assert AdminRole.VIEWER.value == "viewer"


def test_admin_role_is_str_enum() -> None:
    # StrEnum：成員即字串，可直接與字串比較（DB 存字串值）
    assert AdminRole("viewer") is AdminRole.VIEWER
    assert AdminRole.VIEWER == "viewer"
