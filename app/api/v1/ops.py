"""运维观测 API（Phase 0 / G-15 + Phase 1 / G-18）

提供「同步产物新鲜度」与「最近同步终态」两个只读视图，用于回答：
  · 数据是不是真的同步成功了（而不是"记了 success 但 0 条"）
  · 哪张表的数据已经过期
"""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.calculation import CalculationResult
from app.models.daily_sales_stat import DailySalesStat
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.inventory import InventorySnapshot
from app.models.profit_report_stat import ProfitReportStat
from app.models.purchase_order_board import PurchaseOrderBoard
from app.models.purchase_plan_items import PurchasePlanItem
from app.models.sales import SalesData
from app.models.sync_log import SyncLog

router = APIRouter()

# (标签, 模型, 日期列, 允许滞后天数, 说明)
_FRESHNESS_CHECKS = [
    ("products", None, None, 0, "产品档案由同步任务回写，无独立日期列（用 sync_logs 判断）"),
    ("领星销售快照", DailySalesSnapshot, DailySalesSnapshot.snapshot_date, 0, "showOnline 每日快照"),
    ("库存快照", InventorySnapshot, InventorySnapshot.snapshot_date, 0, "FBA 库存每日快照"),
    ("逐日销量", DailySalesStat, DailySalesStat.stat_date, 1, "领星 sales-statistics 逐日"),
    ("销量明细", SalesData, SalesData.date, 1, "快照转日明细"),
    ("计算结果", CalculationResult, CalculationResult.calc_date, 0, "每日主流程产出"),
    ("采购计划明细", PurchasePlanItem, PurchasePlanItem.fetch_date, 0, "listNew 全量替换（失败会保留旧日期）"),
    ("采购单看板", PurchaseOrderBoard, PurchaseOrderBoard.fetch_date, 0, "purchaseOrderBoard 全量替换"),
    ("经营利润报表", ProfitReportStat, ProfitReportStat.stat_date, 1, "按日报，滞后 1 天属正常"),
]


@router.get("/sync-freshness", summary="同步产物新鲜度 + 最近同步终态")
async def sync_freshness(
    recent: int = Query(200, ge=20, le=1000, description="用于汇总最近终态的 sync_logs 条数"),
    session: AsyncSession = Depends(get_session),
):
    today = date.today()
    items = []
    for label, model, col, allowed_lag, note in _FRESHNESS_CHECKS:
        if model is None:
            items.append({
                "table": label, "latest_date": None, "age_days": None,
                "allowed_lag_days": allowed_lag, "ok": None, "note": note,
            })
            continue
        latest = (await session.execute(select(func.max(col)))).scalar()
        age = (today - latest).days if latest else None
        items.append({
            "table": label,
            "latest_date": latest.isoformat() if latest else None,
            "age_days": age,
            "allowed_lag_days": allowed_lag,
            "ok": bool(latest) and age is not None and age <= allowed_lag,
            "note": note,
        })

    # 最近同步终态：每个 sync_type 取最新一条（含 partial/failed/0 条提示）
    rows = (await session.execute(
        select(SyncLog).order_by(SyncLog.id.desc()).limit(recent)
    )).scalars().all()
    latest_by_type: dict[str, dict] = {}
    for r in rows:
        if r.sync_type in latest_by_type:
            continue
        latest_by_type[r.sync_type] = {
            "sync_type": r.sync_type,
            "status": r.status,
            "total_count": r.total_count,
            "success_count": r.success_count,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "duration_ms": r.duration_ms,
            "run_id": r.run_id,
            "error_message": (r.error_message or "")[:500] or None,
            "stats_json": (r.stats_json or "")[:4000] or None,
        }

    bad = [s for s in latest_by_type.values() if s["status"] != "success"]
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "freshness": items,
        "stale_tables": [i["table"] for i in items if i["ok"] is False],
        "latest_sync": sorted(latest_by_type.values(), key=lambda x: x["sync_type"]),
        "abnormal_sync": bad,
        "healthy": not [i for i in items if i["ok"] is False] and not bad,
    }


@router.get("/alerts", summary="需要人工处理的同步告警清单")
async def sync_alerts(
    stale_running_hours: int = Query(2, ge=1, le=48, description="running 超过该小时数视为异常"),
    recent: int = Query(300, ge=20, le=2000),
    session: AsyncSession = Depends(get_session),
):
    """把"需要人管"的问题汇成一个清单（可被前端/巡检脚本直接消费）。

    覆盖：① 产物过期 ② 最近一次同步 partial/failed ③ 长期 running ④ 成功但 0 条
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    rows = (await session.execute(
        select(SyncLog).order_by(SyncLog.id.desc()).limit(recent)
    )).scalars().all()

    latest_by_type: dict[str, SyncLog] = {}
    for r in rows:
        if r.sync_type not in latest_by_type:
            latest_by_type[r.sync_type] = r

    alerts: list[dict] = []
    for t, r in latest_by_type.items():
        if r.status in ("failed", "partial"):
            alerts.append({
                "level": "error" if r.status == "failed" else "warning",
                "kind": "sync_status",
                "target": t,
                "detail": f"最近一次 {t} 终态={r.status}",
                "error_message": (r.error_message or "")[:500] or None,
                "at": r.started_at.isoformat() if r.started_at else None,
            })
        elif r.status == "success" and r.total_count == 0 and t not in (
            "monthly_lingxing", "profit", "acos",  # 这三类"无待回填"时 0 条属正常
        ):
            alerts.append({
                "level": "warning", "kind": "zero_rows", "target": t,
                "detail": f"最近一次 {t} 成功但 0 条，疑似上游异常",
                "at": r.started_at.isoformat() if r.started_at else None,
            })

    # 长期 running
    cutoff = now - timedelta(hours=stale_running_hours)
    stuck = (await session.execute(
        select(SyncLog).where(SyncLog.status == "running", SyncLog.started_at < cutoff)
    )).scalars().all()
    for r in stuck:
        alerts.append({
            "level": "error", "kind": "stuck_running", "target": r.sync_type,
            "detail": f"{r.sync_type} 自 {r.started_at} 起一直 running（>{stale_running_hours}h）",
            "at": r.started_at.isoformat() if r.started_at else None,
        })

    # 产物新鲜度
    fresh = await sync_freshness(recent=recent, session=session)
    for t in fresh["stale_tables"]:
        alerts.append({"level": "error", "kind": "stale_table", "target": t,
                       "detail": f"{t} 数据已过期", "at": fresh["checked_at"]})

    order = {"error": 0, "warning": 1}
    alerts.sort(key=lambda a: order.get(a["level"], 9))
    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "count": len(alerts),
        "errors": len([a for a in alerts if a["level"] == "error"]),
        "alerts": alerts,
    }


@router.get("/sync-run/{run_id}", summary="按 run_id 查看一次同步的父/子记录")
async def sync_run_detail(run_id: str, session: AsyncSession = Depends(get_session)):
    """返回该 run_id 的任务级记录 + 步骤级子记录（Phase 1 / G-16）。"""
    rows = (await session.execute(
        select(SyncLog).where(SyncLog.run_id == run_id).order_by(SyncLog.id)
    )).scalars().all()
    if rows:
        # 兼容早期子记录：只写了 parent_id、未写 run_id
        parent_ids = [r.id for r in rows]
        extra = (await session.execute(
            select(SyncLog).where(SyncLog.parent_id.in_(parent_ids))
        )).scalars().all()
        seen = {r.id for r in rows}
        rows = rows + [r for r in extra if r.id not in seen]
    if not rows:
        return {"run_id": run_id, "found": 0, "task": None, "steps": []}
    task = next((r for r in rows if (r.level or "task") == "task"), rows[0])

    def _dump(r: SyncLog) -> dict:
        return {
            "id": r.id, "sync_type": r.sync_type, "status": r.status, "level": r.level,
            "step": r.step, "parent_id": r.parent_id, "source": r.source,
            "total_count": r.total_count, "success_count": r.success_count,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "duration_ms": r.duration_ms,
            "error_message": (r.error_message or "")[:800] or None,
            "stats_json": (r.stats_json or "")[:4000] or None,
        }

    return {
        "run_id": run_id,
        "found": len(rows),
        "task": _dump(task),
        "steps": [_dump(r) for r in rows if r.id != task.id],
    }
