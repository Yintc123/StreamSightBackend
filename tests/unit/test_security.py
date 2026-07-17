"""Unit tests for app/core/security util（normalize_username 純函式）。§8.1。"""

from app.core.security import normalize_username


def test_normalize_username_strips_and_lowercases() -> None:
    assert normalize_username(" Root ") == "root"


def test_normalize_username_already_normalized_unchanged() -> None:
    assert normalize_username("root") == "root"


def test_normalize_username_uppercase_variants_collapse() -> None:
    assert normalize_username("ADMIN") == normalize_username("admin") == "admin"
