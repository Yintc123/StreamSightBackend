"""Unit tests for RealtimeReadingRepository（realtime-history.md §6.1）。

TDD: RED → GREEN → REFACTOR。每個測試皆依賴 db_session fixture（SQLite in-memory）。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.repositories.repo_realtime_reading import RealtimeReadingRepository


def _ts(offset_seconds: int = 0) -> datetime:
    """建 naive UTC datetime（模擬 streamer 存入格式）。"""
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=offset_seconds)


@pytest.mark.asyncio
async def test_bulk_insert_count(db_session) -> None:
    """bulk_insert 寫入正確筆數：插入 3 筆 → DB 有 3 筆（§6.1 test 1）。"""
    repo = RealtimeReadingRepository(db_session)
    rows = [{"value": float(i), "ts": _ts(i)} for i in range(3)]
    await repo.bulk_insert(rows)

    results = await repo.list(_ts(-10), _ts(10), size=100)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_list_range_filter(db_session) -> None:
    """list 範圍查詢：5 筆不同 ts → from_dt/to_dt 只回傳區間內（§6.1 test 2）。"""
    repo = RealtimeReadingRepository(db_session)
    base = _ts(0)
    rows = [{"value": float(i), "ts": base + timedelta(seconds=i * 10)} for i in range(5)]
    await repo.bulk_insert(rows)

    # 只取第 1–3 筆（base+10s ～ base+30s）
    from_dt = base + timedelta(seconds=5)
    to_dt = base + timedelta(seconds=35)
    results = await repo.list(from_dt, to_dt, size=100)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_list_order(db_session) -> None:
    """list 排序：結果按 ts ASC（最舊在前，§6.1 test 3）。"""
    repo = RealtimeReadingRepository(db_session)
    base = _ts(0)
    rows = [{"value": float(i), "ts": base + timedelta(seconds=i)} for i in range(5)]
    await repo.bulk_insert(rows)

    results = await repo.list(base - timedelta(seconds=1), base + timedelta(seconds=10), size=100)
    timestamps = [r.ts for r in results]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_list_size_limit(db_session) -> None:
    """list size 上限：插入 100 筆、size=10 → 只回傳 10 筆（§6.1 test 4）。"""
    repo = RealtimeReadingRepository(db_session)
    base = _ts(0)
    rows = [{"value": float(i), "ts": base + timedelta(seconds=i)} for i in range(100)]
    await repo.bulk_insert(rows)

    results = await repo.list(base - timedelta(seconds=1), base + timedelta(seconds=200), size=10)
    assert len(results) == 10


@pytest.mark.asyncio
async def test_bulk_insert_empty(db_session) -> None:
    """bulk_insert 空 list：不拋例外、DB 筆數不變（§6.1 test 5）。"""
    repo = RealtimeReadingRepository(db_session)
    await repo.bulk_insert([])  # 不應拋例外

    results = await repo.list(_ts(-10), _ts(10), size=100)
    assert len(results) == 0
