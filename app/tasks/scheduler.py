"""APScheduler 调度器配置"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.tasks.sync_tasks import sync_products, sync_sales_data, sync_inventory
from app.tasks.calculation_tasks import run_batch_calculation


def setup_scheduler() -> AsyncIOScheduler:
    """设置定时任务调度器"""
    scheduler = AsyncIOScheduler()

    # 数据同步任务（每24小时执行一次）
    sync_interval = settings.SYNC_INTERVAL_HOURS
    scheduler.add_job(
        sync_products,
        trigger=IntervalTrigger(hours=sync_interval),
        id="sync_products",
        name="同步产品数据",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_sales_data,
        trigger=IntervalTrigger(hours=sync_interval),
        id="sync_sales",
        name="同步销量数据",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_inventory,
        trigger=IntervalTrigger(hours=sync_interval),
        id="sync_inventory",
        name="同步库存数据",
        replace_existing=True,
    )

    # 批量计算任务（每日定时执行）
    hour, minute = settings.DAILY_REPORT_TIME.split(":")
    scheduler.add_job(
        run_batch_calculation,
        trigger=CronTrigger(hour=int(hour), minute=int(minute)),
        id="batch_calculation",
        name="批量计算采购建议",
        replace_existing=True,
    )

    return scheduler
