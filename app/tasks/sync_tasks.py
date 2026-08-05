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
        from scraper.lingxing_product_scraper import scrape_all, merge_jsonl_to_json
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

        from scripts.daily_sales_snapshot import take_snapshot
        from scripts.snapshot_to_daily import snapshot_to_daily
        await asyncio.to_thread(take_snapshot)
        records = await snapshot_to_daily()

        async with session:
            _record_result(log, session, "success", total=records, success=records)
            await session.commit()
        logger.info(f"销量数据同步完成: {records} 条日明细")
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error(f"销量数据同步失败: {e}")
    finally:
        await session.close()


async def sync_inventory():
    """同步FBA库存：领星 MCP query_fba_valid_list 全量快照（每日幂等）"""
    log = SyncLog(sync_type="inventory", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_fba_stock import sync_fba_stock
        count = await sync_fba_stock()

        async with session:
            _record_result(log, session, "success", total=count, success=count)
            await session.commit()
        logger.info(f"FBA库存同步完成: {count} 条")
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

        from sqlalchemy import func, select
        from app.models.product import Product
        count = (await session.execute(
            select(func.count()).select_from(Product).where(Product.box_quantity.isnot(None))
        )).scalar() or 0

        async with session:
            _record_result(log, session, "success", total=count, success=count)
            await session.commit()
        logger.info(f"箱规数据同步完成: {count} 条")
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error(f"箱规数据同步失败: {e}")
    finally:
        await session.close()
