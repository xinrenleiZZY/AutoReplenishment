"""数据同步定时任务"""

import logging

from app.services.lx_api import LxApiClient
from app.database import async_session_factory
from app.models.sync_log import SyncLog
from app.models.product import Product

logger = logging.getLogger(__name__)


async def sync_products():
    """同步产品数据"""
    log = SyncLog(sync_type="product", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

            client = LxApiClient()
            products = await client.get_products()

            count = 0
            for item in products:
                # 更新或创建产品
                product = Product(
                    asin=item["asin"],
                    product_name=item.get("product_name", ""),
                    category=item.get("category"),
                )
                await session.merge(product)
                count += 1

            log.status = "success"
            log.total_count = count
            log.success_count = count
            log.completed_at = __import__("datetime").datetime.now()
            await session.commit()

        logger.info(f"产品同步完成: {count} 条")
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        log.completed_at = __import__("datetime").datetime.now()
        await session.commit()
        logger.error(f"产品同步失败: {e}")
    finally:
        await session.close()


async def sync_sales_data():
    """同步销量数据"""
    log = SyncLog(sync_type="sales", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

            client = LxApiClient()
            count = await client.sync_all_sales(session)

            log.status = "success"
            log.total_count = count
            log.success_count = count
            log.completed_at = __import__("datetime").datetime.now()
            await session.commit()

        logger.info(f"销量数据同步完成: {count} 条")
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        log.completed_at = __import__("datetime").datetime.now()
        await session.commit()
        logger.error(f"销量数据同步失败: {e}")
    finally:
        await session.close()


async def sync_inventory():
    """同步库存数据"""
    log = SyncLog(sync_type="inventory", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

            client = LxApiClient()
            count = await client.sync_all_inventory(session)

            log.status = "success"
            log.total_count = count
            log.success_count = count
            log.completed_at = __import__("datetime").datetime.now()
            await session.commit()

        logger.info(f"库存数据同步完成: {count} 条")
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        log.completed_at = __import__("datetime").datetime.now()
        await session.commit()
        logger.error(f"库存数据同步失败: {e}")
    finally:
        await session.close()
