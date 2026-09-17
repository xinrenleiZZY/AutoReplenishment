"""数据同步定时任务（真实数据管道）

每天固定时间执行，接的是实际在用的数据源：
  - 产品基础信息：领星 showOnline 全量抓取（scraper）→ 安全更新基础字段
  - 销量数据：每日销量快照（showOnline）→ 快照转 sales_data 日明细
  - FBA 库存：领星 MCP query_fba_valid_list 全量快照
  - 箱规：领星 product/lists 抓取 → products.box_quantity
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

from app.database import async_session_factory
from app.models.sync_log import SyncLog
from app.services.raw_store import flush_raw

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)


def _record_result(log, session, status, total=None, success=None, error=None):
    """记录同步结果并提交"""
    log.status = status
    if total is not None:
        log.total_count = total
    if success is not None:
        log.success_count = success
    if error:
        log.error_message = str(error)[:2000]
    log.completed_at = datetime.now()
    session.add(log)


async def sync_products():
    """同步产品基础信息：showOnline 全量抓取 → 安全更新基础字段（不覆盖等级/工期/箱规等维护字段）"""
    log = SyncLog(sync_type="product", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        # 1) 全量抓取 JSONL 并合并为 JSON
        from app.services.config_service import get_param
        from scraper.lingxing_product_scraper import scrape_all, merge_jsonl_to_json
        mode = (await get_param(session, "listing_sync_mode")) or "full"
        create_start = (await get_param(session, "listing_create_start")) or ""
        create_end = (await get_param(session, "listing_create_end")) or ""
        if mode == "by_create_time":
            await asyncio.to_thread(scrape_all, create_start, create_end)
        else:
            await asyncio.to_thread(scrape_all)
        await asyncio.to_thread(merge_jsonl_to_json)

        # 2) 安全导入（已存在→更新基础字段；新品→插入）
        from scripts.import_lingxing_data import import_products
        json_path = os.path.join(BASE_DIR, "p_id", "msku_id_full.json")
        count = await import_products(json_path)

        async with session:
            _record_result(log, session, "success", total=count, success=count)
            await session.commit()
        logger.info(f"产品同步完成: {count} 条")
        await flush_raw()
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error(f"产品同步失败: {e}")
    finally:
        await session.close()


async def sync_sales_data():
    """同步销量数据：每日快照抓取（幂等，同日已抓取则跳过）→ 快照转 sales_data 日明细"""
    log = SyncLog(sync_type="sales", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.daily_sales_snapshot import take_snapshot, save_snapshots_to_db
        from scripts.snapshot_to_daily import snapshot_to_daily
        # 抓取放线程（同步 requests），写库在主事件循环，避免跨 loop 连接池冲突
        snapshots, snap_date = await asyncio.to_thread(take_snapshot, write_db=False)
        if snapshots:
            await save_snapshots_to_db(snapshots, snap_date)
        records = await snapshot_to_daily()

        async with session:
            _record_result(log, session, "success", total=records, success=records)
            await session.commit()
        logger.info(f"销量数据同步完成: {records} 条日明细")
        await flush_raw()
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error(f"销量数据同步失败: {e}")
    finally:
        await session.close()


async def sync_inventory():
    """同步FBA库存：showOnline 优先 → MCP 兜底 → 重试 showOnline（最多3次）

    通道优先级（2026-08-24 调整）：
      1. showOnline（领星网页 API，LX_AUTH_TOKEN，与销量同源，最新最全）
      2. 拉取不到(0条/失败) → 领星 MCP（query_fba_valid_list / get_fba_stock_list）
      3. 仍失败 → 重试 showOnline（最多 3 次）
    保证库存数据始终是最新的。
    """
    log = SyncLog(sync_type="inventory", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_fba_stock_web import sync_fba_stock_web
        from scripts.sync_fba_stock import sync_fba_stock

        # 1) showOnline 优先（鉴权失败抛异常也按"拉取不到"处理，继续 MCP 兜底）
        count = 0
        source = "无"
        try:
            count = await sync_fba_stock_web()
            source = "showOnline"
        except Exception as e:
            logger.warning(f"FBA库存 showOnline 通道失败({e})")
        # 2) 拉取不到 → MCP 兜底
        if count <= 0:
            try:
                count = await sync_fba_stock()
                source = "MCP"
            except Exception as e:
                logger.warning(f"FBA库存 MCP 通道失败({e})")
        # 3) 仍失败 → 重试 showOnline（最多3次）
        retries = 0
        while count <= 0 and retries < 3:
            retries += 1
            logger.warning(f"FBA库存 第{retries}次重试 showOnline...")
            try:
                count = await sync_fba_stock_web()
                source = f"showOnline重试{retries}"
            except Exception as e:
                logger.warning(f"FBA库存 showOnline 重试{retries}失败({e})")
        if count <= 0:
            raise RuntimeError("FBA库存同步全部通道失败（showOnline×1+MCP+showOnline×3）")

        async with session:
            _record_result(log, session, "success", total=count, success=count)
            await session.commit()
        logger.info(f"FBA库存同步完成: {count} 条（{source}）")
        await flush_raw()
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error(f"FBA库存同步失败: {e}")
    finally:
        await session.close()


async def sync_box_quantity():
    """同步箱规数据（领星 product/lists → products.box_quantity，只更新 >0 的产品）"""
    log = SyncLog(sync_type="box_quantity", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scraper.lingxing_box_qty_scraper import run_sync
        await asyncio.to_thread(run_sync)

        # 多源合并：产品管理(product_name/分类/状态/箱规) + listing(品牌/店铺/售价等) 刷新最全字段
        from scripts.merge_product_sources import run_merge
        await run_merge()

        from sqlalchemy import func, select
        from app.models.product import Product
        count = (await session.execute(
            select(func.count()).select_from(Product).where(Product.box_quantity.isnot(None))
        )).scalar() or 0

        async with session:
            _record_result(log, session, "success", total=count, success=count)
            await session.commit()
        logger.info(f"箱规数据同步完成: {count} 条")
        await flush_raw()
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error(f"箱规数据同步失败: {e}")
    finally:
        await session.close()


async def sync_purchase_orders():
    """同步待到货量：采购订单 orderListsV2 首选，失败/未匹配到数据回退库存明细 storage/lists 的 pending_num"""
    log = SyncLog(sync_type="purchase_orders", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_purchase_orders import main as sync_po

        stats = await sync_po()
        await flush_raw()
        matched = stats.get("asins", 0) if isinstance(stats, dict) else 0
        if matched:
            async with session:
                _record_result(log, session, "success", total=matched, success=matched)
                await session.commit()
            logger.info("待到货量同步完成（采购订单首选）: %s", stats)
            return stats
        logger.warning("采购订单通道未匹配到数据（%s），回退库存明细通道", stats)
    except Exception as e:  # noqa: BLE001
        logger.warning("采购订单通道异常（%s），回退库存明细通道", e)
    try:
        from scripts.sync_pending_stock import sync_pending_stock

        stats = await sync_pending_stock()
        await flush_raw()
        if stats.get("ok"):
            total = stats.get("ok", 0)
            async with session:
                _record_result(log, session, "success", total=total, success=total)
                await session.commit()
            logger.info("待到货量同步完成（库存明细兜底）: %s", stats)
            return stats
        logger.warning("库存明细通道未匹配到数据（%s）", stats)
    except Exception as e:  # noqa: BLE001
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("待到货量同步失败: %s", e)
    finally:
        await session.close()
