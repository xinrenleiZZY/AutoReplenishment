"""APScheduler 调度器配置"""

import logging
import json
import uuid
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import async_session_factory
from app.models.sync_log import SyncLog
from app.tasks.sync_tasks import (
    sync_products, sync_sales_data, sync_inventory, sync_box_quantity, sync_purchase_orders,
    sync_purchase_order_items,
)
from app.tasks.calculation_tasks import run_due_calculation

logger = logging.getLogger(__name__)

# 全局调度器引用（供 API 动态注册一次性定时任务，如任务大厅的单ASIN定时私发）
_scheduler: AsyncIOScheduler | None = None


def set_scheduler(scheduler: AsyncIOScheduler) -> None:
    """应用启动时登记全局调度器实例"""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> AsyncIOScheduler | None:
    """获取全局调度器实例（未启动时返回 None）"""
    return _scheduler


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


# ──────────────────────────────────────────────
#  终态判定（Phase 0 / G-14 + Phase 1 / G-16）
# ──────────────────────────────────────────────

# 这些 key 出现非 0/非空即代表"本步有进展"，用于区分 partial 与 failed
_PROGRESS_KEYS = ("written", "updated", "total", "rows", "matched_asins",
                  "plan_items_written", "board_written", "asins", "classified")


def _classify_sync_result(stats: dict | None, error_keys=(), zero_keys=()) -> tuple[str, str]:
    """纯函数：根据脚本返回的 stats 判定 (status, error_message)。

    规则（Phase 0 / G-14）：
      · error_keys 中任一非空，或 stats["errors"] 非空 → 有错误
      · 有错误 且 有进展 → partial；有错误 且 无进展 → failed
      · 无错误 但 zero_keys 中任一为 0/空 → partial（"成功但 0 条"不再记为 success）
      · 其余 → success
    """
    stats = stats or {}
    errors: list[str] = []
    for key in error_keys:
        val = stats.get(key)
        if not val:
            continue
        if isinstance(val, (list, tuple, set)):
            errors.extend(f"{key}: {str(x)[:300]}" for x in val if x)
        else:
            errors.append(f"{key}: {str(val)[:300]}")
    extra = stats.get("errors")
    if isinstance(extra, (list, tuple, set)):
        errors.extend(str(x)[:300] for x in extra if x)
    elif isinstance(extra, str) and extra.strip():
        errors.append(extra[:300])

    zeros = [k for k in zero_keys if not stats.get(k)]
    has_progress = any(bool(stats.get(k)) for k in _PROGRESS_KEYS)

    if errors:
        status = "partial" if has_progress else "failed"
        msg = "；".join(dict.fromkeys(errors))
        return status, msg[:2000]
    if zeros:
        return "partial", f"关键产物为 0（{', '.join(zeros)}）：疑似上游抓取失败或未落库，请核查"
    return "success", ""


def _finalize(log, session, stats, *, source=None, run_id=None, total=None, success=None,
              error_keys=(), zero_keys=(), label=""):
    """按 stats 判定终态并写库（含 stats_json / duration_ms）"""
    status, message = _classify_sync_result(stats, error_keys=error_keys, zero_keys=zero_keys)
    log.run_id = log.run_id or run_id or new_run_id()
    log.status = status
    log.stats_json = json.dumps(stats, ensure_ascii=False, default=str)[:60000] if stats else None
    if source:
        log.source = source
    if run_id:
        log.run_id = run_id
    if total is None and isinstance(stats, dict):
        total = stats.get("total")
    if success is None and isinstance(stats, dict):
        success = stats.get("written") or stats.get("updated")
    log.total_count = total
    log.success_count = success
    log.error_message = message or None
    log.completed_at = datetime.now()
    if log.started_at:
        log.duration_ms = int((log.completed_at - log.started_at).total_seconds() * 1000)
    session.add(log)
    if status != "success":
        logger.warning("同步任务 %s 终态=%s %s", log.sync_type, status, message[:200])
    return status, message


def new_run_id() -> str:
    """生成一次运行的关联 ID（Phase 1 / G-16）"""
    return uuid.uuid4().hex[:16]


async def _write_step_rows(session, parent_id: int, sync_type: str, stats: dict | None,
                           source: str | None = None, run_id: str | None = None) -> int:
    """把 stats["steps"] 落成步骤级子记录（Phase 1 / G-16）。

    约定：脚本在 stats["steps"] 里给出 {步骤名: {"ok": bool, ...}}，
    本函数为每一步写一行 sync_logs（level="step"，parent_id=父行 id），
    这样"哪一步慢/哪一步错"可以直接按 run_id 查询。
    """
    steps = (stats or {}).get("steps") if isinstance(stats, dict) else None
    if not isinstance(steps, dict) or not steps:
        return 0
    n = 0
    for name, info in steps.items():
        info = info if isinstance(info, dict) else {"detail": info}
        ok = bool(info.get("ok"))
        err = info.get("error")
        row = SyncLog(
            sync_type=sync_type,
            status="success" if ok else "failed",
            level="step",
            step=str(name)[:40],
            parent_id=parent_id,
            run_id=run_id,          # 与父行同 run_id，便于按 run 聚合查询
            source=source,
            total_count=info.get("total") or info.get("written") or info.get("matched_asins"),
            success_count=info.get("written") or info.get("matched_asins"),
            error_message=(str(err)[:2000] if err else None),
            stats_json=json.dumps(info, ensure_ascii=False, default=str)[:10000],
        )
        session.add(row)
        n += 1
    return n


async def mark_stale_running_interrupted(max_hours: int = 6) -> int:
    """把超过 max_hours 仍处于 running 的记录标记为 interrupted（进程被杀/无终态）"""
    from sqlalchemy import select, update

    cutoff = datetime.now() - timedelta(hours=max_hours)
    async with async_session_factory() as session:
        res = await session.execute(
            update(SyncLog)
            .where(SyncLog.status == "running", SyncLog.started_at < cutoff)
            .values(status="interrupted",
                    completed_at=datetime.now(),
                    error_message="进程中断：超过 %d 小时未写入终态，已由启动巡检标记" % max_hours)
        )
        await session.commit()
        n = res.rowcount or 0
    if n:
        logger.warning("已标记 %d 条超时 running 记录为 interrupted", n)
    return n


async def run_daily_flow():
    """每日主流程：基础数据刷新（新老品→等级→节日→生命周期）→ 按等级频率计算到期产品 → 推送日报"""
    try:
        from app.database import async_session_factory
        from app.services.foundation import refresh_foundation

        async with async_session_factory() as session:
            await refresh_foundation(session)
    except Exception as e:  # noqa: BLE001
        logger.error("每日基础数据刷新失败，继续计算: %s", e)
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
            # 自动生成 AI 日报总结（启用且可用时；失败回退规则日报，不影响推送）
            from app.services import ai_eval

            if ai_eval.ai_enabled():
                try:
                    summary = await ai_eval.generate_daily_report_ai(summary, session)
                except Exception as e:
                    logger.warning(f"AI 日报生成失败，使用规则日报推送: {e}")
        ok = await notifier.send_daily_report(summary)
        logger.info("日报推送飞书: %s", "成功" if ok else "失败")
        # 日报可视化大屏图片（失败不影响文本日报）
        if ok:
            from app.services.report_image import generate_and_send
            try:
                await generate_and_send(notifier)
            except Exception as e:
                logger.error(f"日报大屏图片发送失败: {e}")
        # 当日流程产生的 SIF/MCP 原始响应统一落库
        try:
            from app.services.raw_store import flush_raw

            await flush_raw()
        except Exception as e:  # noqa: BLE001
            logger.error(f"API原始数据归档失败: {e}")
    except Exception as e:
        logger.error(f"日报推送失败: {e}")
    return stats


async def sync_profit_rates():
    """每日兜底回填利润率为空的产品（领星 MCP 毛利报表）

    profit_rate 主来源为经营利润报表（sync_profit_report）；本任务仅补齐报表未覆盖（仍为空）的产品，
    因此排在经营利润报表之后执行。
    """
    log = SyncLog(sync_type="profit", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_lx_profit import backfill as backfill_profit

        stats = await backfill_profit(limit=100000, offset=0, days=30, all_=False)
        filled = (stats or {}).get("updated", 0) if isinstance(stats, dict) else 0
        async with session:
            # 0 条属合法（无待回填），但 errors 非空必须暴露
            _finalize(log, session, stats, total=filled, success=filled, error_keys=("errors",))
            await session.commit()
        logger.info("利润回填完成: %s", stats)
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("利润回填失败: %s", e)
    finally:
        await session.close()


async def sync_acos():
    """每日回填 ACOS 为空的产品（历史月度表/领星利润报表），避免批量计算逐产品调外部接口"""
    log = SyncLog(sync_type="acos", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_acos import backfill as backfill_acos

        stats = await backfill_acos(limit=100000, offset=0, all_=False)
        filled = (stats or {}).get("updated", 0) if isinstance(stats, dict) else 0
        async with session:
            _finalize(log, session, stats, total=filled, success=filled, error_keys=("errors",))
            await session.commit()
        logger.info("ACOS 回填完成: %s", stats)
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("ACOS 回填失败: %s", e)
    finally:
        await session.close()


async def sync_monthly_lingxing():
    """每日回填领星月度历史（销量/毛利/退货/ACOS），数据源：领星利润报表"""
    log = SyncLog(sync_type="monthly_lingxing", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.backfill_monthly_history import run_backfill

        stats = await run_backfill()
        added = (stats or {}).get("lingxing", 0) if isinstance(stats, dict) else 0
        async with session:
            _finalize(log, session, stats, total=added, success=added, error_keys=("errors",))
            await session.commit()
        logger.info("领星月度回填完成: %s", stats)
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("领星月度回填失败: %s", e)
    finally:
        await session.close()


async def sync_base_analysis():
    """基础数据分析：每日基础数据源同步完成后触发，刷新 等级→节日→生命周期"""
    log = SyncLog(sync_type="base_analysis", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from app.services.foundation import refresh_foundation

        async with async_session_factory() as s2:
            stats = await refresh_foundation(s2)
        total = (stats or {}).get("total", 0) if isinstance(stats, dict) else 0
        async with session:
            _finalize(log, session, stats, total=total, success=total, zero_keys=("total",))
            await session.commit()
        logger.info("基础数据分析完成: %s", stats)
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("基础数据分析失败: %s", e)
    finally:
        await session.close()


async def sync_fx_rate():
    """每日实时汇率同步（Google Finance USD/CNY）"""
    log = SyncLog(sync_type="fx_rate", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_fx_rate import main as sync_fx

        await sync_fx()
        async with session:
            _record_result(log, session, "success", total=1, success=1)
            await session.commit()
        logger.info("实时汇率同步完成")
    except Exception as e:
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("实时汇率同步失败: %s", e)
    finally:
        await session.close()


async def sync_sales_statistics():
    """每日同步销售统计报表（领星 sales-statistics/report/list，今年 1/1 ~ 今日）"""
    log = SyncLog(sync_type="sales_statistics", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_sales_statistics import sync_sales_statistics as _sync

        today = date.today()
        stats = await _sync(start=f"{today.year}-01-01", end=today.isoformat(),
                            query_type="volume", group_type="asin", dry_run=False)
        total = (stats or {}).get("total", 0) if isinstance(stats, dict) else 0
        async with session:
            # 抓取异常时脚本把原因放进 stats["errors"]；total=0 视为异常（年初至今不应为 0）
            _finalize(log, session, stats, total=total, success=total,
                      error_keys=("errors",), zero_keys=("total",))
            await session.commit()
        logger.info("销售统计同步完成: %s", stats)
    except Exception as e:  # noqa: BLE001
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("销售统计同步失败: %s", e)
    finally:
        await session.close()


async def sync_daily_sales():
    """每日同步逐日销量（领星 sales-statistics/report/list，filterDateType=day）

    首次全量回填去年1/1~今日；之后每次增量更新近三天（今天/昨天/前天）。
    """
    log = SyncLog(sync_type="daily_sales", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_daily_sales import backfill, daily_update, needs_backfill

        if await needs_backfill():
            stats = await backfill()
        else:
            stats = await daily_update()
        written = (stats or {}).get("written", 0) if isinstance(stats, dict) else 0
        async with session:
            _finalize(log, session, stats, total=written, success=written,
                      error_keys=("errors",), zero_keys=("written",))
            await session.commit()
        logger.info("逐日销量同步完成: %s", stats)
    except Exception as e:  # noqa: BLE001
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("逐日销量同步失败: %s", e)
    finally:
        await session.close()


async def sync_profit_report():
    """每日同步领星经营利润报表毛利率（bd/profit/report/report/asin/list，按单日日报）

    抓取最近 3 天（昨天为主 + 兜底近两天延迟结算），按 stat_date 幂等全量覆盖，写入 profit_report_stats；
    随后把报表毛利率回填到 products.profit_rate（profit_rate 的主要来源，覆盖写入）。
    """
    log = SyncLog(sync_type="profit_report", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_profit_report import backfill_products_profit_rate, sync_range as sync_profit_range

        today = date.today()
        start = (today - timedelta(days=3)).isoformat()
        end = (today - timedelta(days=1)).isoformat()
        stats = await sync_profit_range(start, end, dry_run=False)
        # 报表毛利率回填 products.profit_rate（主来源）
        bf = await backfill_products_profit_rate()
        written = (stats or {}).get("written", 0) if isinstance(stats, dict) else 0
        async with session:
            _finalize(log, session, stats, total=written, success=(bf or {}).get("updated", 0),
                      error_keys=("errors",), zero_keys=("written",))
            await session.commit()
        logger.info("经营利润报表同步完成: %s | profit_rate 回填: %s", stats, bf)
    except Exception as e:  # noqa: BLE001
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("经营利润报表同步失败: %s", e)
    finally:
        await session.close()


async def sync_purchase_sources():
    """每日凌晨同步领星 采购计划 + 采购单看板（两个网页API全量入库）"""
    log = SyncLog(sync_type="purchase_sources", status="running")
    session = async_session_factory()
    try:
        async with session:
            session.add(log)
            await session.commit()

        from scripts.sync_purchase_sources import main as _sync

        stats = await _sync(dry_run=False)
        total = (
            (stats or {}).get("plan_items_written", 0)
            + (stats or {}).get("board_written", 0)
        )
        async with session:
            # Phase 0 / G-14：任一步骤失败 → partial/failed，并写入 error_message
            _finalize(log, session, stats, total=total, success=total,
                      source=(stats or {}).get("source"),
                      error_keys=("plan_items_error", "board_error", "wait_error", "errors"),
                      zero_keys=("plan_items_written", "board_written"))
            await session.commit()
            # Phase 1 / G-16：步骤级子记录（计划明细 / 看板 / 待到货量）
            try:
                await _write_step_rows(session, log.id, "purchase_sources", stats,
                                       source=(stats or {}).get("source"), run_id=log.run_id)
                await session.commit()
            except Exception as e:  # noqa: BLE001  子记录失败不影响主流程
                logger.warning("写入步骤级记录失败: %s", e)
        logger.info("采购计划/采购单看板同步完成: %s", stats)
    except Exception as e:  # noqa: BLE001
        async with session:
            _record_result(log, session, "failed", error=e)
            await session.commit()
        logger.error("采购计划/采购单看板同步失败: %s", e)
    finally:
        await session.close()


async def refresh_asin_list_if_pending():
    """ASIN 列表手动操作后的刷新任务（每 10 分钟检查一次）

    仅在检测到「待刷新」标记（asin_list_refresh_pending=1）时执行：
    用已落盘的领星全量 JSON 重跑导入清洗（B–E），不重新抓取（A 阶段），
    使移出保留/排除列表的 ASIN 按清洗规则重新判定状态。
    """
    import os

    from app.tasks.sync_tasks import BASE_DIR

    session = async_session_factory()
    try:
        from app.services.config_service import get_param, set_param

        pending = str(await get_param(session, "asin_list_refresh_pending") or "0").strip()
        if pending != "1":
            return
        json_path = os.path.join(BASE_DIR, "p_id", "msku_id_full.json")
        if not os.path.exists(json_path):
            logger.warning("ASIN 列表刷新跳过：缺少全量文件 %s", json_path)
            return
        from scripts.import_lingxing_data import import_products

        count = await import_products(json_path)
        await set_param(session, "asin_list_refresh_pending", "0")
        logger.info("ASIN 列表刷新完成：重新清洗 %s 条", count)
    except Exception as e:  # noqa: BLE001
        logger.error("ASIN 列表刷新失败: %s", e)
    finally:
        await session.close()


async def roll_festival_years_task():
    """节日日历年份自更新：每年 12-31 23:59 三日期字段年份 +1"""
    from app.services.config_service import get_param, set_param
    from app.services.festival_year_roll import BASE_YEAR_PARAM, roll_festival_years

    session = async_session_factory()
    try:
        base = int(await get_param(session, BASE_YEAR_PARAM) or 0)
        await roll_festival_years(session, 1)
        await set_param(session, BASE_YEAR_PARAM, (base or date.today().year) + 1)
    except Exception as e:  # noqa: BLE001
        logger.error("节日日历年份自更新失败: %s", e)
    finally:
        await session.close()


def setup_scheduler() -> AsyncIOScheduler:
    """设置定时任务调度器"""
    scheduler = AsyncIOScheduler()

    # 数据同步任务（主数据全量同步窗口 07:00–08:20，80 分钟内错开以避开领星API并发限流）
    sync_hour, sync_minute = (int(x) for x in settings.SYNC_TIME.split(":"))
    sync_base = sync_hour * 60 + sync_minute
    sync_jobs = [
        (sync_products, 0, "sync_products", "同步产品数据"),
        (sync_sales_data, 16, "sync_sales", "同步销量数据"),
        (sync_inventory, 32, "sync_inventory", "同步库存数据"),
        (sync_purchase_orders, 48, "sync_purchase_orders", "同步待到货量"),
        (sync_box_quantity, 64, "sync_box_quantity", "同步箱规数据"),
        (sync_sales_statistics, 80, "sync_sales_statistics", "同步销售统计"),
    ]
    for job, offset, job_id, name in sync_jobs:
        total = sync_base + offset
        scheduler.add_job(
            job,
            trigger=CronTrigger(hour=(total // 60) % 24, minute=total % 60),
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

    # 每日基础数据补充：领星月度 + 经营利润报表(→profit_rate 主来源) + ACOS + 利润兜底（08:22 起，主同步窗口 08:20 结束后、基础分析 08:45 之前）
    daily_foundation_jobs = [
        (sync_monthly_lingxing, 25, "sync_monthly_lingxing", "领星月度回填(每日)"),
        (sync_profit_report, 28, "sync_profit_report", "经营利润报表→profit_rate(每日,主)"),
        (sync_acos, 31, "sync_acos", "ACOS回填(每日)"),
        (sync_daily_sales, 34, "sync_daily_sales", "逐日销量(每日)"),
        (sync_profit_rates, 37, "sync_profit_rates", "利润兜底(每日)"),
        (sync_purchase_sources, 40, "sync_purchase_sources", "采购计划+listNew+采购单看板(每日)"),
        (lambda: sync_purchase_order_items(mode="recent60"), 43, "sync_purchase_order_items", "采购单产品明细(每日近60天)"),
    ]
    for job, minute, job_id, name in daily_foundation_jobs:
        scheduler.add_job(
            job,
            trigger=CronTrigger(hour=8, minute=minute),
            id=job_id,
            name=name,
            replace_existing=True,
        )

    # 基础数据分析：每日基础数据源同步完成后触发一次（等级→节日→生命周期）
    scheduler.add_job(
        sync_base_analysis,
        trigger=CronTrigger(hour=8, minute=45),
        id="sync_base_analysis",
        name="基础数据分析(每日)",
        replace_existing=True,
    )

    scheduler.add_job(
        sync_fx_rate,
        trigger=CronTrigger(hour=8, minute=22),
        id="sync_fx_rate",
        name="实时汇率同步(每日)",
        replace_existing=True,
    )

    # ASIN 列表刷新：手动改动保留/排除列表后 10 分钟内重新清洗状态
    scheduler.add_job(
        refresh_asin_list_if_pending,
        trigger=CronTrigger(minute="*/10"),
        id="refresh_asin_list",
        name="ASIN列表刷新(每10分钟)",
        replace_existing=True,
    )

    # 节日日历年更新：每年 12-31 23:59 三日期字段年份 +1（启动时另有漏跑兜底）
    scheduler.add_job(
        roll_festival_years_task,
        trigger=CronTrigger(month=12, day=31, hour=23, minute=59),
        id="roll_festival_years",
        name="节日日历年更新(每年12-31)",
        replace_existing=True,
    )

    return scheduler
