"""独立调度服务入口（Phase 3 / B-03）

容器内用法：`python -m app.scheduler_service`

职责（与 API 进程完全解耦）：
  · 独占 APScheduler：每日同步/基础数据/主流程/节假滚年等定时任务（定义仍在 app/tasks/scheduler.py）
  · 启动巡检：同步日志的超时 running 标记 + 任务大厅单ASIN任务的孤儿 running 标记
  · 每 30 秒轮询 task_jobs，把 API 登记的"定时任务大厅-单ASIN"任务注册进来
    （API 侧 RUN_SCHEDULER_IN_API=false，不再往进程内调度器加任务）

与 API 的边界：**同一时刻只能有一个调度器在跑**。API 通过 RUN_SCHEDULER_IN_API 控制，
compose 里 api 设为 false、本服务独占。
"""

import asyncio
import logging
import signal
from datetime import datetime

from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services import job_store
from app.tasks.scheduler import mark_stale_running_interrupted, set_scheduler, setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("app.scheduler_service")

_registered: set[str] = set()


def _task_dict(p: dict) -> dict:
    return {
        "id": p["task_id"],
        "asin": p["asin"],
        "operator": p.get("operator"),
        "run_at": p["run_at"],
        "status": "scheduled",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": None,
        "finished_at": None,
        "stats": None,
        "error": None,
    }


async def _register_pending_singles(scheduler) -> int:
    """把 API 登记、尚未注册的定时私发任务加进调度器（幂等：已注册的跳过）。"""
    added = 0
    try:
        from app.api.v1.calculation import _run_single_notify_task

        for p in await job_store.pending_scheduled_singles():
            tid = p.get("task_id")
            if not tid or tid in _registered or not p.get("asin") or not p.get("run_at"):
                continue
            try:
                run_dt = datetime.fromisoformat(str(p["run_at"]))
            except ValueError:
                logger.warning("定时时间格式非法，跳过：task_id=%s run_at=%s", tid, p.get("run_at"))
                continue
            if run_dt <= datetime.now():
                continue
            scheduler.add_job(
                _run_single_notify_task,
                trigger=DateTrigger(run_date=run_dt),
                args=[_task_dict(p)],
                id=f"task_hall_single_{tid}",
                name=f"单ASIN私发 {p['asin']}",
                replace_existing=True,
                misfire_grace_time=600,
            )
            _registered.add(tid)
            added += 1
            logger.info("已注册定时私发任务：%s @ %s（task_id=%s）", p["asin"], run_dt, tid)
    except Exception as e:  # noqa: BLE001
        logger.error("注册定时任务失败: %s", e)
    return added


async def main() -> None:
    scheduler = setup_scheduler()
    set_scheduler(scheduler)
    scheduler.start()
    jobs = scheduler.get_jobs()
    logger.info("调度服务已启动：共 %d 个定时任务", len(jobs))
    for j in jobs:
        logger.info("  · %s | next_run=%s", j.name or j.id, getattr(j, "next_run_time", None))

    # 启动巡检（只清自己负责的那类任务，避免误标 API 正在跑的任务）
    n1 = await mark_stale_running_interrupted(6)
    n2 = await job_store.mark_orphan_running_interrupted(only_kinds=("task-hall-single",))
    logger.info("启动巡检：超时同步记录 %d 条、遗留定时私发任务 %d 条 标记为 interrupted", n1, n2)

    added = await _register_pending_singles(scheduler)
    logger.info("启动时恢复定时私发任务 %d 个", added)

    # 定时轮询：API 新登记的定时任务由这里接管注册
    # 注意：AsyncIOScheduler 要求直接提交协程函数，不能用
    # lambda + create_task（执行器线程里没有 running loop，会 RuntimeError）
    async def _poll_pending_singles() -> None:
        await _register_pending_singles(scheduler)

    scheduler.add_job(
        _poll_pending_singles,
        trigger=IntervalTrigger(seconds=30),
        id="b03_poll_pending_singles",
        name="轮询待注册的定时私发任务（30s）",
        replace_existing=True,
        max_instances=1,
    )

    stop = asyncio.Event()

    def _shutdown(*_args):
        logger.info("收到退出信号，准备关闭调度服务…")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:  # Windows
            signal.signal(sig, _shutdown)
    await stop.wait()
    scheduler.shutdown(wait=False)
    logger.info("调度服务已停止")


if __name__ == "__main__":
    asyncio.run(main())
