"""APScheduler 调度器配置"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.tasks.sync_tasks import sync_products, sync_sales_data, sync_inventory, sync_box_quantity
from app.tasks.calculation_tasks import run_due_calculation

logger = logging.getLogger(__name__)


async def run_daily_flow():
    """每日主流程：按等级频率计算到期产品 → 推送日报到飞书"""
    stats = await run_due_calculation()
    try:
        from app.integrations.feishu import FeishuNotifier
        from app.tasks.calculation_tasks import get_daily_summary
        from app.database import async_session_factory

        notifier = FeishuNotifier()
        if not notifier.app_mode and not notifier.webhook_url:
            logger.info("飞书通知未配置，跳过日报推送")
            return stats

        async with async_session_factory() as session:
            summary = await get_daily_summary(session)
        ok = await notifier.send_daily_report(summary)
        logger.info("日报推送飞书: %s", "成功" if ok else "失败")
    except Exception as e:
        logger.error(f"日报推送失败: {e}")
    return stats


def setup_scheduler() -> AsyncIOScheduler:
    """设置定时任务调度器"""
    scheduler = AsyncIOScheduler()

    # 数据同步任务（每天固定时间执行，错开15分钟避免领星API并发限流）
    sync_hour, sync_minute = (int(x) for x in settings.SYNC_TIME.split(":"))
    sync_jobs = [
        (sync_products, 0, "sync_products", "同步产品数据"),
        (sync_sales_data, 15, "sync_sales", "同步销量数据"),
        (sync_inventory, 30, "sync_inventory", "同步库存数据"),
        (sync_box_quantity, 45, "sync_box_quantity", "同步箱规数据"),
    ]
    for job, offset, job_id, name in sync_jobs:
        scheduler.add_job(
            job,
            trigger=CronTrigger(hour=sync_hour, minute=(sync_minute + offset) % 60),
            id=job_id,
            name=name,
            replace_existing=True,
        )

    # 每日主流程（每天固定时间执行：按等级频率计算到期产品 → 推送飞书日报）
    hour, minute = (int(x) for x in settings.DAILY_REPORT_TIME.split(":"))
    scheduler.add_job(
        run_daily_flow,
        trigger=CronTrigger(hour=int(hour), minute=int(minute)),
        id="daily_flow",
        name="按等级频率计算 + 推送日报",
        replace_existing=True,
    )

    return scheduler
