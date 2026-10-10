"""上游数据门禁与熔断（Phase 3 / B-05）

目的：上游同步失败/产物过期时，**不再照常出采购建议**。

两层判定：
  1) 新鲜度门禁：关键表（销售快照/库存快照/采购计划明细）最新日期超出允许滞后 → 阻断；
     非关键表（逐日销量/经营利润报表等）滞后 → 只告警不阻断。
  2) 熔断：关键同步类型（product/sales/inventory/purchase_sources）**连续 N 次非成功**
     （failed/partial/interrupted）→ 判为上游不可信，阻断。

阻断记录写入 sync_logs（sync_type=data_gate），因此会自动出现在
/api/v1/ops/sync-freshness 的 abnormal_sync 与 /api/v1/ops/alerts 告警清单里。
"""

import json
import logging
from datetime import date, datetime

from sqlalchemy import func, select

from app.models.calculation import CalculationResult
from app.models.daily_sales_stat import DailySalesStat
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.inventory import InventorySnapshot
from app.models.profit_report_stat import ProfitReportStat
from app.models.purchase_order_board import PurchaseOrderBoard
from app.models.purchase_plan_items import PurchasePlanItem
from app.models.sales import SalesData
from app.models.sync_log import SyncLog

logger = logging.getLogger(__name__)

# 关键表（缺失/过期会直接让采购建议失真 → 阻断）；其余表滞后只告警
CRITICAL_TABLES = {"领星销售快照", "库存快照", "采购计划明细"}

# 关键同步类型（连续失败 → 熔断）
CRITICAL_SYNC_TYPES = ("product", "sales", "inventory", "purchase_sources")
BREAKER_THRESHOLD = 3      # 连续 3 次非成功即熔断
BREAKER_LOOKBACK = 12      # 只看最近 12 条 sync_logs

# (标签, 模型, 日期列, 允许滞后天数, 说明)——与 G-15 新鲜度接口同源
FRESHNESS_CHECKS = [
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


async def collect_freshness(session) -> list[dict]:
    """各关键表最新日期与滞后判定（与 /api/v1/ops/sync-freshness 同一口径）。"""
    today = date.today()
    items = []
    for label, model, col, allowed_lag, note in FRESHNESS_CHECKS:
        if model is None:
            items.append({"table": label, "latest_date": None, "age_days": None,
                          "allowed_lag_days": allowed_lag, "ok": None, "note": note})
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
    return items


async def collect_breaker(session, lookback: int = BREAKER_LOOKBACK,
                          threshold: int = BREAKER_THRESHOLD) -> list[dict]:
    """关键同步类型的"连续非成功"计数（熔断依据）。"""
    rows = (await session.execute(
        select(SyncLog).order_by(SyncLog.id.desc()).limit(lookback)
    )).scalars().all()
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r.sync_type, []).append(r)
    out = []
    for t in CRITICAL_SYNC_TYPES:
        runs = by_type.get(t, [])
        if not runs:
            out.append({"sync_type": t, "consecutive_bad": 0, "tripped": False,
                        "last_status": None, "detail": "无记录（视为首次运行，不熔断）"})
            continue
        bad = 0
        for r in runs:
            if r.status in ("failed", "partial", "interrupted"):
                bad += 1
            else:
                break
        out.append({
            "sync_type": t, "consecutive_bad": bad, "tripped": bad >= threshold,
            "last_status": runs[0].status,
            "detail": f"最近连续 {bad} 次非成功（阈值 {threshold}）",
        })
    return out


def decide(freshness: list[dict], breaker: list[dict],
           critical_tables: set[str] | None = None) -> dict:
    """纯函数：根据新鲜度与熔断结果给出"放行/阻断"（便于单测）。"""
    critical = critical_tables if critical_tables is not None else CRITICAL_TABLES
    reasons: list[str] = []
    warnings: list[str] = []
    for item in freshness:
        if item.get("ok") is False:
            msg = (f"{item['table']} 最新日期 {item.get('latest_date') or '无'}，"
                   f"超出允许滞后 {item.get('allowed_lag_days')} 天")
            (reasons if item["table"] in critical else warnings).append(msg)
    for b in breaker:
        if b.get("tripped"):
            reasons.append(f"熔断：{b['sync_type']} {b.get('detail')}")
    return {
        "ok": not reasons,
        "blocked": bool(reasons),
        "reasons": reasons,
        "warnings": warnings,
        "checks": freshness,
        "breaker": breaker,
    }


async def evaluate(session) -> dict:
    """采集 + 判定（只读，不写库）。"""
    freshness = await collect_freshness(session)
    breaker = await collect_breaker(session)
    result = decide(freshness, breaker)
    result["checked_at"] = datetime.now().isoformat(timespec="seconds")
    return result


async def record(session, result: dict, *, source: str = "scheduler") -> None:
    """把门禁结论写入 sync_logs（自动进入新鲜度/告警视图）。"""
    now = datetime.now()
    ok_count = sum(1 for c in result.get("checks", []) if c.get("ok"))
    status = "success" if result.get("ok") else "skipped"
    msg = ("数据门禁阻断：" + "；".join(result.get("reasons", [])))[:2000] if result.get("reasons") else None
    log = SyncLog(
        sync_type="data_gate",
        status=status,
        level="task",
        source=source,
        total_count=len(result.get("checks", [])),
        success_count=ok_count,
        error_message=msg,
        stats_json=json.dumps({"reasons": result.get("reasons"), "warnings": result.get("warnings"),
                               "breaker": result.get("breaker"), "checks": result.get("checks")},
                              ensure_ascii=False, default=str)[:60000],
        started_at=now,
        completed_at=now,
        duration_ms=0,
    )
    session.add(log)
    await session.commit()
