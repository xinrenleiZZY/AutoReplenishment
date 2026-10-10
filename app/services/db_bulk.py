"""products 批量写入的统一入口（Phase 1 / G-17）

背景：sync_fba_stock_web / sync_purchase_orders / sync_pending_stock /
sync_asin_wait_quantity 都在"一个长事务里逐行 UPDATE products"，彼此并发时
会产生死锁（2026-10-08 13:58 的 purchase_orders 失败即为 DeadlockDetectedError）。

本模块提供统一写法：
  · 按 asin 升序写入（所有写入方加锁顺序一致，消除交叉等待）
  · 每块 500 行、每块一个独立短事务（缩短持锁时间）
  · 事务内 SET LOCAL lock_timeout（避免无限等待）
  · 死锁/序列化/锁超时 自动重试（指数退避）
"""

import asyncio
import logging
from datetime import date, datetime

from sqlalchemy import bindparam, text

from app.models.inventory import InventorySnapshot
from app.models.product import Product

logger = logging.getLogger(__name__)

# 用 Core 表对象发 UPDATE：
#   · 避免 ORM「按主键批量更新」语义（executemany 时要求参数里带主键，实际会报
#     "No primary key value supplied for column(s) products.asin"）；
#   · 避免 ORM 同步会话对象（"bulk synchronize of persistent objects not supported"）。
_PROD = Product.__table__
_ZERO_STMT = (
    _PROD.update()
    .where(_PROD.c.purchase_on_order.isnot(None), _PROD.c.purchase_on_order != 0)
    .values(purchase_on_order=0)
)
_UPD_STMT = (
    _PROD.update()
    .where(_PROD.c.asin == bindparam("b_asin"))
    .values(purchase_on_order=bindparam("b_qty"))
)

# FBA 三列（sync_fba_stock_web）：同样按 asin 升序 + 分块，避免 4767 行长事务夹在
# purchase_orders / pending_stock 之间造成死锁（Phase 1 / G-17）。
_FBA_UPD_STMT = (
    _PROD.update()
    .where(_PROD.c.asin == bindparam("b_asin"))
    .values(
        afn_fulfillable_quantity=bindparam("b_avail"),
        afn_reserved_quantity=bindparam("b_reserved"),
        afn_inbound_shipped_quantity=bindparam("b_inbound"),
        updated_at=bindparam("b_updated_at"),
    )
)

_SNAP = InventorySnapshot.__table__
_SNAP_DEL = _SNAP.delete().where(_SNAP.c.snapshot_date == bindparam("b_date"))
_SNAP_INS = _SNAP.insert()

CHUNK_SIZE = 500
MAX_RETRIES = 3
LOCK_TIMEOUT_MS = 5000

_RETRYABLE = ("DeadlockDetected", "SerializationFailure", "LockNotAvailable", "LockTimeout")


def _is_retryable(exc: Exception) -> bool:
    name = type(exc).__name__
    if any(k in name for k in _RETRYABLE):
        return True
    text_ = str(exc)
    return any(k in text_ for k in _RETRYABLE)


async def _set_lock_timeout(session) -> None:
    try:
        await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'"))
    except Exception:  # noqa: BLE001  非 PG 或权限不足时忽略
        pass


async def update_products_purchase_on_order(session_factory, rows: dict[str, int]) -> int:
    """把 {asin: qty} 批量写回 products.purchase_on_order（分块 + 重试）。返回写入行数。"""
    items = sorted((str(a), int(q)) for a, q in (rows or {}).items() if a)
    if not items:
        return 0

    written = 0
    for i in range(0, len(items), CHUNK_SIZE):
        chunk = items[i:i + CHUNK_SIZE]
        params = [{"b_asin": a, "b_qty": q} for a, q in chunk]
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session_factory() as s:
                    await _set_lock_timeout(s)
                    await s.execute(_UPD_STMT, params)
                    await s.commit()
                written += len(chunk)
                break
            except Exception as e:  # noqa: BLE001
                if attempt >= MAX_RETRIES or not _is_retryable(e):
                    logger.error("批量写 products.purchase_on_order 失败（第%d块，已试%d次）: %s",
                                 i // CHUNK_SIZE + 1, attempt, e)
                    raise
                await asyncio.sleep(0.5 * attempt)
                logger.warning("products.purchase_on_order 第%d块 第%d次重试（%s）",
                               i // CHUNK_SIZE + 1, attempt, type(e).__name__)
    return written


async def reset_products_purchase_on_order(session_factory) -> int:
    """把 purchase_on_order 非 0 的行清零（短事务，替代原来的全表 UPDATE）。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session_factory() as s:
                await _set_lock_timeout(s)
                res = await s.execute(_ZERO_STMT)
                await s.commit()
                return res.rowcount or 0
        except Exception as e:  # noqa: BLE001
            if attempt >= MAX_RETRIES or not _is_retryable(e):
                raise
            await asyncio.sleep(0.5 * attempt)
            logger.warning("清零 purchase_on_order 第%d次重试（%s）", attempt, type(e).__name__)
    return 0


async def replace_purchase_on_order(session_factory, rows: dict[str, int]) -> int:
    """**原子替换** products.purchase_on_order（Phase 1 / G-17 修正版）。

    为什么需要这个函数：先前"先清零、再分批写入"是两段独立事务，
    若第二步失败，会把所有产品的待到货量清成 0（2026-10-08 17:0x 实际发生过）。
    本函数把"清零 + 写入"放进**同一个事务**，并保持：
      · 按 asin 升序写入（统一加锁顺序）
      · 分块 500 行
      · lock_timeout + 死锁自动重试
    要么整体生效，要么整体回滚，不会出现"清零了但没写回去"的中间态。
    """
    items = sorted((str(a), int(q)) for a, q in (rows or {}).items() if a)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session_factory() as s:
                await _set_lock_timeout(s)
                await s.execute(_ZERO_STMT)
                for i in range(0, len(items), CHUNK_SIZE):
                    chunk = items[i:i + CHUNK_SIZE]
                    await s.execute(_UPD_STMT, [{"b_asin": a, "b_qty": q} for a, q in chunk])
                await s.commit()
            return len(items)
        except Exception as e:  # noqa: BLE001
            if attempt >= MAX_RETRIES or not _is_retryable(e):
                logger.error("原子替换 purchase_on_order 失败（已试 %d 次）: %s", attempt, e)
                raise
            await asyncio.sleep(0.5 * attempt)
            logger.warning("原子替换 purchase_on_order 第%d次重试（%s）", attempt, type(e).__name__)
    return 0


async def update_products_fba_stock(session_factory,
                                    rows: dict[str, tuple[int, int, int]]) -> int:
    """把 {asin: (FBA可售, FBA预留, FBA在途)} 批量写回 products（Phase 1 / G-17）。

    替代 `sync_fba_stock_web` 原先"单事务逐行 UPDATE 4767 行"的写法：
      · 按 asin 升序（与 purchase_orders / pending_stock 加锁顺序一致）
      · 每 500 行一个短事务 + SET LOCAL lock_timeout
      · 死锁/序列化失败自动重试
    返回成功写入的 ASIN 数。
    """
    items = sorted((str(a), v) for a, v in (rows or {}).items() if a)
    if not items:
        return 0

    written = 0
    for i in range(0, len(items), CHUNK_SIZE):
        chunk = items[i:i + CHUNK_SIZE]
        now = datetime.now()
        params = [{"b_asin": a, "b_avail": int(v[0]), "b_reserved": int(v[1]),
                   "b_inbound": int(v[2]), "b_updated_at": now} for a, v in chunk]
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session_factory() as s:
                    await _set_lock_timeout(s)
                    await s.execute(_FBA_UPD_STMT, params)
                    await s.commit()
                written += len(chunk)
                break
            except Exception as e:  # noqa: BLE001
                if attempt >= MAX_RETRIES or not _is_retryable(e):
                    logger.error("批量写 products FBA 库存失败（第%d块，已试%d次）: %s",
                                 i // CHUNK_SIZE + 1, attempt, e)
                    raise
                await asyncio.sleep(0.5 * attempt)
                logger.warning("products FBA 库存 第%d块 第%d次重试（%s）",
                               i // CHUNK_SIZE + 1, attempt, type(e).__name__)
    return written


async def replace_inventory_snapshot(session_factory, snapshot_date: date,
                                     rows: dict[str, dict]) -> int:
    """**原子替换**某一日的 inventory_snapshots（Phase 1 / G-17）。

    rows: {asin: {"fba_available":int, "fba_reserved":int, "fba_inbound":int}}
    "删除当日 + 分批插入"在**同一事务**内完成（幂等），并按 asin 升序插入，
    避免先删后插两步之间失败导致当日快照整体丢失。
    """
    items = sorted((str(a), v or {}) for a, v in (rows or {}).items() if a)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session_factory() as s:
                await _set_lock_timeout(s)
                await s.execute(_SNAP_DEL, {"b_date": snapshot_date})
                now = datetime.now()
                for i in range(0, len(items), CHUNK_SIZE):
                    chunk = items[i:i + CHUNK_SIZE]
                    await s.execute(_SNAP_INS, [
                        {
                            "asin": a,
                            "snapshot_date": snapshot_date,
                            "fba_available": int(v.get("fba_available") or 0),
                            "fba_reserved": int(v.get("fba_reserved") or 0),
                            "fba_inbound": int(v.get("fba_inbound") or 0),
                            "fba_inbound_shipped": int(v.get("fba_inbound") or 0),
                            "local_stock": int(v.get("local_stock") or 0),
                            "purchase_on_order": int(v.get("purchase_on_order") or 0),
                            "created_at": now,
                        }
                        for a, v in chunk
                    ])
                await s.commit()
            return len(items)
        except Exception as e:  # noqa: BLE001
            if attempt >= MAX_RETRIES or not _is_retryable(e):
                logger.error("原子替换 inventory_snapshots(%s) 失败（已试 %d 次）: %s",
                             snapshot_date, attempt, e)
                raise
            await asyncio.sleep(0.5 * attempt)
            logger.warning("inventory_snapshots(%s) 第%d次重试（%s）",
                           snapshot_date, attempt, type(e).__name__)
    return 0
