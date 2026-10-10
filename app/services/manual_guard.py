"""手工触发隔离与限流（Phase 3 / B-06）

两个约束（避免手工操作抢定时任务的外部接口配额、避免并发跑批）：
  1) **定时窗口隔离**：每日 07:00–09:00 是产品/销量/库存/采购来源的定时同步窗口，
     此时段的"手工全量同步"默认拒绝（可用 force=true 显式强制）。
  2) **串行 + 最小间隔**：同一进程内同时只允许 1 个"重任务"（全量同步/批量计算），
     同类任务两次触发之间至少间隔 MIN_INTERVAL_SECONDS 秒。

窗口与间隔均可通过环境变量覆盖（MANUAL_BLOCK_WINDOW_START/END、MANUAL_MIN_INTERVAL_SECONDS）。
"""

import asyncio
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


SYNC_WINDOW_START = _env_int("MANUAL_BLOCK_WINDOW_START", 7)
SYNC_WINDOW_END = _env_int("MANUAL_BLOCK_WINDOW_END", 9)
MIN_INTERVAL_SECONDS = _env_int("MANUAL_MIN_INTERVAL_SECONDS", 60)


def in_sync_window(now: datetime | None = None) -> bool:
    """是否处于每日定时同步窗口 [START, END)。"""
    now = now or datetime.now()
    return SYNC_WINDOW_START <= now.hour < SYNC_WINDOW_END


def window_reason(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return (f"当前处于每日定时同步窗口 {SYNC_WINDOW_START:02d}:00–{SYNC_WINDOW_END:02d}:00"
            f"（现在 {now:%H:%M}），手工全量同步会抢占定时任务的外部接口配额。"
            "如需强制执行请加 force=true。")


class ManualGate:
    """进程内"重任务"闸门：串行执行 + 同类型最小间隔。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._running: str | None = None
        self._started_at: float | None = None
        self._last_finished: dict[str, float] = {}

    def snapshot(self) -> dict:
        now = time.time()
        return {
            "running": self._running,
            "running_seconds": int(now - self._started_at) if self._started_at else None,
            "min_interval_seconds": MIN_INTERVAL_SECONDS,
            "in_sync_window": in_sync_window(),
            "last_finished": {k: datetime.fromtimestamp(v).isoformat(timespec="seconds")
                              for k, v in self._last_finished.items()},
        }

    async def acquire(self, kind: str) -> tuple[bool, str]:
        """尝试占用闸门；返回 (是否拿到, 拒绝原因)。"""
        if self._lock.locked():
            return False, (f"已有重任务在执行（{self._running}），请等它结束再触发"
                           f"（B-06 限流：重任务串行）")
        await self._lock.acquire()
        last = self._last_finished.get(kind)
        if last is not None and time.time() - last < MIN_INTERVAL_SECONDS:
            self._lock.release()
            wait = int(MIN_INTERVAL_SECONDS - (time.time() - last))
            return False, f"同类任务 {kind} 距上次结束不足 {MIN_INTERVAL_SECONDS} 秒（还需 {wait}s）"
        self._running = kind
        self._started_at = time.time()
        return True, ""

    def release(self, kind: str) -> None:
        if self._running == kind:
            self._running = None
            self._started_at = None
        self._last_finished[kind] = time.time()
        if self._lock.locked():
            self._lock.release()


manual_gate = ManualGate()
