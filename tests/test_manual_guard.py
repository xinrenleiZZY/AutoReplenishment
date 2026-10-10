"""B-06 手工触发隔离与限流（窗口判定 + 串行 + 最小间隔）"""

import asyncio
from datetime import datetime

from app.services import manual_guard


def test_in_sync_window_boundaries():
    assert manual_guard.in_sync_window(datetime(2026, 10, 10, 7, 0)) is True
    assert manual_guard.in_sync_window(datetime(2026, 10, 10, 8, 59)) is True
    assert manual_guard.in_sync_window(datetime(2026, 10, 10, 6, 59)) is False
    assert manual_guard.in_sync_window(datetime(2026, 10, 10, 9, 0)) is False
    assert manual_guard.in_sync_window(datetime(2026, 10, 10, 14, 30)) is False


def test_window_reason_mentions_window_and_force():
    reason = manual_guard.window_reason(datetime(2026, 10, 10, 8, 30))
    assert "07:00" in reason and "09:00" in reason and "force" in reason


def test_acquire_serializes_heavy_tasks():
    async def _run():
        gate = manual_guard.ManualGate()
        ok1, _ = await gate.acquire("a")
        assert ok1 is True
        ok2, reason = await gate.acquire("b")
        assert ok2 is False and "串行" in reason
        gate.release("a")
        ok3, _ = await gate.acquire("b")
        assert ok3 is True
        gate.release("b")

    asyncio.run(_run())


def test_min_interval_between_same_kind(monkeypatch):
    monkeypatch.setattr(manual_guard, "MIN_INTERVAL_SECONDS", 60)

    async def _run():
        gate = manual_guard.ManualGate()
        ok1, _ = await gate.acquire("due")
        assert ok1 is True
        gate.release("due")
        ok2, reason = await gate.acquire("due")
        assert ok2 is False and "不足" in reason
        # 不同 kind 不受同类型间隔影响
        ok3, _ = await gate.acquire("batch")
        assert ok3 is True
        gate.release("batch")

    asyncio.run(_run())


def test_snapshot_reports_state():
    gate = manual_guard.ManualGate()
    snap = gate.snapshot()
    assert snap["running"] is None
    assert snap["min_interval_seconds"] == manual_guard.MIN_INTERVAL_SECONDS
    assert "in_sync_window" in snap
