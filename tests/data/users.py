"""User seed data for tests and future DB seeding.

Format: array of dicts, each with an `_id` metadata field for named lookup.
        Loaders in `tests/payloads.py` strip `_id` before returning payloads.

Add new users by appending to the list. Each row must have:
    - `_id`: unique lookup key (any string)
    - real DB fields (email, name, ...) matching the User schema
"""

from typing import Any


USERS: list[dict[str, Any]] = [
    {"_id": "yin",   "email": "yin_test@example.com", "name": "yin"},
    {"_id": "bob",   "email": "bob@example.com",      "name": "bob"},
    {"_id": "alice", "email": "alice@example.com",    "name": "Alice"},
    {"_id": "a",     "email": "a@example.com",        "name": "A"},
    {"_id": "b",     "email": "b@example.com",        "name": "B"},
]


INVALID_PAYLOADS: list[dict[str, Any]] = [
    {"_id": "invalid_email", "email": "not-an-email", "name": "X"},
]
