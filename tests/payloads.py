"""Test payload loaders from Python seed modules.

Seeds live in `tests/data/*.py` as typed Python lists — imported directly
(no JSON parsing, no file I/O overhead).

Each seed row may include an `_id` metadata field for named lookup;
loaders strip metadata (any key starting with `_`) before returning
the payload — so the returned dict is a clean record ready for API
requests or DB inserts.

Test usage:
    from tests.payloads import user_payload

    payload = user_payload("yin")                    # Named lookup
    payload = user_payload("yin", name="Renamed")    # Override fields

DB seeding usage:
    from tests.payloads import user_seeds_all

    async def seed_db(session):
        for data in user_seeds_all():
            session.add(User(**data))
        await session.commit()

Adding new seed categories:
    1. Create `tests/data/<category>.py` with a list of dicts (see users.py).
    2. Add a loader function below (same pattern as `user_payload`).
"""

import copy
from typing import Any

from tests.data.users import INVALID_PAYLOADS, USERS

_METADATA_PREFIX: str = "_"


def _strip_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Remove metadata fields (keys starting with `_`)."""
    return {k: v for k, v in row.items() if not k.startswith(_METADATA_PREFIX)}


def _find_by_id(rows: list[dict[str, Any]], seed: str, **overrides: Any) -> dict[str, Any]:
    """Find a row by `_id`, strip metadata, deep-copy, apply overrides."""
    for row in rows:
        if row.get("_id") == seed:
            payload: dict[str, Any] = copy.deepcopy(_strip_metadata(row))
            payload.update(overrides)
            return payload
    available: list[str] = [str(r.get("_id")) for r in rows if "_id" in r]
    raise KeyError(f"Unknown seed {seed!r}. Available: {available}")


def _all_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return all rows with metadata stripped — ready for bulk INSERT."""
    return [copy.deepcopy(_strip_metadata(row)) for row in rows]


# ────────────────────────────────────────────────
# Test lookup: single record by _id
# ────────────────────────────────────────────────
def user_payload(seed: str, **overrides: Any) -> dict[str, Any]:
    """Load a named user payload.

    Args:
        seed: matches `_id` field in tests/data/users.py USERS
        **overrides: fields to override in the returned payload

    Example:
        user_payload("yin")                    # {"email": "...", "name": "yin"}
        user_payload("yin", name="Renamed")    # override name
    """
    return _find_by_id(USERS, seed, **overrides)


def invalid_payload(seed: str, **overrides: Any) -> dict[str, Any]:
    """Load a named invalid payload for negative testing."""
    return _find_by_id(INVALID_PAYLOADS, seed, **overrides)


# ────────────────────────────────────────────────
# DB seeding: bulk records (metadata stripped)
# ────────────────────────────────────────────────
def user_seeds_all() -> list[dict[str, Any]]:
    """Return all user payloads (metadata stripped), ready for bulk INSERT.

    Example:
        for data in user_seeds_all():
            session.add(User(**data))
    """
    return _all_records(USERS)
