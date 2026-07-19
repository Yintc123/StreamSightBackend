"""Unit tests for RealtimeStreamer + sample_value（realtime-stream.md §6.1/§6.2）。

TDD: RED（測試先寫、功能後補）→ GREEN → REFACTOR。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.realtime.streamer import RealtimeStreamer, sample_value

# ── §6.1 sample_value ────────────────────────────────────────────────────────


def test_sample_value_deterministic() -> None:
    """決定性：同 tick 多次呼叫結果相同。"""
    assert sample_value(5) == sample_value(5)
    assert sample_value(0) == sample_value(0)
    assert sample_value(9999) == sample_value(9999)


def test_sample_value_range() -> None:
    """值域 [0.0, 100.0]，一位小數。"""
    for tick in range(100):
        v = sample_value(tick)
        assert 0.0 <= v <= 100.0
        assert round(v, 1) == v


def test_sample_value_different_seeds() -> None:
    """相同 tick 不同 seed → 通常不同值（決定性演算法）。"""
    assert sample_value(5, seed=0) != sample_value(5, seed=1)


# ── §6.2 RealtimeStreamer.run ─────────────────────────────────────────────────


def _make_publisher() -> MagicMock:
    pub = MagicMock()
    pub.to_topic = AsyncMock()
    return pub


@pytest.mark.asyncio
async def test_streamer_publishes_once(fake_redis) -> None:
    """發佈一次：一次 sleep cycle → publisher.to_topic 呼叫一次，payload 正確（§6.2 test 4）。"""
    pub = _make_publisher()
    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis)
    await streamer.start()
    await asyncio.sleep(1.2)
    await streamer.stop()

    pub.to_topic.assert_called()
    call_args = pub.to_topic.call_args
    topic, payload = call_args[0]
    assert topic == "realtime.stream"
    assert payload["type"] == "data"
    assert payload["topic"] == "realtime.stream"
    assert isinstance(payload["value"], float)
    assert "ts" in payload
    # ts 為 ISO 8601 含 UTC offset（+00:00）
    assert "+00:00" in payload["ts"]


@pytest.mark.asyncio
async def test_streamer_tick_increments(fake_redis) -> None:
    """tick 遞增：連跑兩次 cycle，Redis realtime:tick 值遞增（§6.2 test 5）。"""
    pub = _make_publisher()
    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis)
    await streamer.start()
    await asyncio.sleep(2.2)
    await streamer.stop()

    tick_val = int(await fake_redis.get("realtime:tick"))
    assert tick_val >= 2


@pytest.mark.asyncio
async def test_publisher_exception_doesnt_stop_task(fake_redis) -> None:
    """Publisher 例外不停 task：拋 Exception 後 task 仍在跑（§6.2 test 6）。"""
    pub = MagicMock()
    pub.to_topic = AsyncMock(side_effect=Exception("boom"))

    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis)
    await streamer.start()
    await asyncio.sleep(2.2)

    assert streamer._task is not None
    assert not streamer._task.done()

    await streamer.stop()


@pytest.mark.asyncio
async def test_cancelled_error_propagates(fake_redis) -> None:
    """CancelledError 正常退出：stop() 後 task 完成（§6.2 test 7）。"""
    pub = _make_publisher()
    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis)
    await streamer.start()
    await asyncio.sleep(0.1)
    await streamer.stop()

    assert streamer._task is not None
    assert streamer._task.done()


def test_topic_registered() -> None:
    """realtime.stream topic 已在 TOPIC_MIN_ROLE 中（§5.1）。"""
    from app.core.enums import AdminRole
    from app.services.ws.topics import TOPIC_MIN_ROLE

    assert TOPIC_MIN_ROLE.get("realtime.stream") == AdminRole.VIEWER


# ── §6.2 batch / flush 邏輯（realtime-history.md §6.2 tests 6–10）────────────


def _make_mock_repo():
    """回傳帶 async bulk_insert 的 mock repo。"""
    repo = MagicMock()
    repo.bulk_insert = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_not_flush_below_60(fake_redis) -> None:
    """未滿 60 筆不 flush：執行 59 次 tick → bulk_insert 未被呼叫（§6.2 test 6）。"""
    pub = _make_publisher()
    repo = _make_mock_repo()
    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis, repo=repo)

    for _ in range(59):
        await streamer._tick()

    repo.bulk_insert.assert_not_called()


@pytest.mark.asyncio
async def test_flush_at_60(fake_redis) -> None:
    """滿 60 筆 flush 一次：執行 60 次 tick → bulk_insert 被呼叫一次，傳入 60 筆（§6.2 test 7）。"""
    pub = _make_publisher()
    repo = _make_mock_repo()
    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis, repo=repo)

    for _ in range(60):
        await streamer._tick()

    repo.bulk_insert.assert_called_once()
    rows = repo.bulk_insert.call_args[0][0]
    assert len(rows) == 60


@pytest.mark.asyncio
async def test_batch_cleared_after_flush(fake_redis) -> None:
    """flush 後 batch 清空：flush 後繼續 1 次 tick → _batch 長度為 1（§6.2 test 8）。"""
    pub = _make_publisher()
    repo = _make_mock_repo()
    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis, repo=repo)

    for _ in range(60):
        await streamer._tick()
    await streamer._tick()

    assert len(streamer._batch) == 1


@pytest.mark.asyncio
async def test_cancelled_flushes_remainder(fake_redis) -> None:
    """CancelledError 觸發 flush：task 跑時 batch 累積 30 筆，stop() 取消 → _loop() CancelledError
    → _flush() → bulk_insert 被呼叫一次，傳入 30 筆（§6.2 test 9）。"""
    from datetime import UTC, datetime

    pub = _make_publisher()
    repo = _make_mock_repo()
    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis, repo=repo)

    await streamer.start()
    # 讓 task 取得一次事件迴圈時間，進入 asyncio.sleep(1.0) 等待狀態
    await asyncio.sleep(0)

    # 趁 task 在 sleep(1.0) 中等待，直接注入 30 筆到 _batch（不需等 30 秒）
    for i in range(30):
        streamer._batch.append({"value": float(i), "ts": datetime.now(UTC).replace(tzinfo=None)})

    # 取消 task → _loop() 在 sleep(1.0) 收到 CancelledError → except 區塊 _flush()
    await streamer.stop()

    repo.bulk_insert.assert_called_once()
    rows = repo.bulk_insert.call_args[0][0]
    assert len(rows) == 30


@pytest.mark.asyncio
async def test_flush_exception_doesnt_stop_task(fake_redis) -> None:
    """flush 例外不停 task：bulk_insert 拋 Exception → task 繼續（§6.2 test 10）。"""
    from datetime import UTC, datetime

    pub = _make_publisher()
    repo = _make_mock_repo()
    repo.bulk_insert = AsyncMock(side_effect=Exception("db error"))
    streamer = RealtimeStreamer(publisher=pub, redis_client=fake_redis, repo=repo)

    await streamer.start()
    await asyncio.sleep(0)  # task 進入 sleep(1.0)

    # 注入 59 筆；第 60 次 _tick() 會觸發 _flush() → bulk_insert 拋例外 → 被 _flush 捕獲
    for i in range(59):
        streamer._batch.append({"value": float(i), "ts": datetime.now(UTC).replace(tzinfo=None)})
    await streamer._tick()  # 第 60 筆 → 觸發 flush → exception 被捕獲

    # task 仍在跑（未因 flush 例外而結束）
    assert streamer._task is not None
    assert not streamer._task.done()

    await streamer.stop()
