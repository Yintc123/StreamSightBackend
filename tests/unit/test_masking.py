import pytest

from app.core.security import mask_email


@pytest.mark.parametrize(
    "email, expected",
    [
        # 一般 email：保留前 2 字 + domain
        ("alice@example.com", "al***@example.com"),
        ("bob@example.com", "bo***@example.com"),
        ("charlie@example.com", "ch***@example.com"),
        # 短 local：只保留 1 字
        ("a@b.com", "a***@b.com"),
        ("ab@example.com", "a***@example.com"),
        # 特殊字元
        ("first.last@example.com", "fi***@example.com"),
        ("user+tag@example.com", "us***@example.com"),
        ("user_name@example.com", "us***@example.com"),
        # 子網域
        ("alice@mail.example.co.uk", "al***@mail.example.co.uk"),
        # Edge cases
        ("", "***"),
        ("invalid", "***"),
        ("no-at-sign", "***"),
        ("@example.com", "***"),      # 只有 domain
        ("alice@", "***"),             # 只有 local
    ],
)
def test_mask_email(email: str, expected: str) -> None:
    assert mask_email(email) == expected
