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
