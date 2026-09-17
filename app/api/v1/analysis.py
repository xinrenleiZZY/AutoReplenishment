"""商品等级分析报告 API 路由"""

from collections import Counter

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.calculation import CalculationResult
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.inventory import InventorySnapshot
from app.models.product import Product
from app.tasks.calculation_tasks import _product_ad_profit_metrics

router = APIRouter()


@router.get("/summary")
async def analysis_summary(
    level: str = Query("S", description="商品等级 S/A/B/C/D"),
    session: AsyncSession = Depends(get_session),
):
    """商品等级分析报告（概览/分布/销量TOP/风险/明细）"""
    level = level.upper()
    prods = (await session.execute(
        select(Product).where(Product.product_level == level, Product.status == True)  # noqa: E712
    )).scalars().all()
    asins = [p.asin for p in prods]

    calcs: dict[str, CalculationResult] = {}
    if asins:
        latest_sub = (
            select(CalculationResult.asin, func.max(CalculationResult.calc_date).label("d"))
            .where(CalculationResult.asin.in_(asins))
            .group_by(CalculationResult.asin)
            .subquery()
        )
        q = (
            select(CalculationResult)
            .join(latest_sub,
                  (CalculationResult.asin == latest_sub.c.asin) & (CalculationResult.calc_date == latest_sub.c.d))
            .where(CalculationResult.asin.in_(asins))
        )
        for r in (await session.execute(q)).scalars().all():
            calcs[r.asin] = r

    snap_date = (await session.execute(select(func.max(DailySalesSnapshot.snapshot_date)))).scalar()
    snaps = {}
    if snap_date and asins:
        for r in (await session.execute(select(DailySalesSnapshot).where(
                DailySalesSnapshot.snapshot_date == snap_date,
                DailySalesSnapshot.asin.in_(asins)))).scalars().all():
            snaps[r.asin] = r

    inv_date = (await session.execute(select(func.max(InventorySnapshot.snapshot_date)))).scalar()
    invs = {}
    if inv_date and asins:
        for r in (await session.execute(select(InventorySnapshot).where(
                InventorySnapshot.snapshot_date == inv_date,
                InventorySnapshot.asin.in_(asins)))).scalars().all():
            invs[r.asin] = r

    items = []
    for p in prods:
        c = calcs.get(p.asin)
        sn = snaps.get(p.asin)
        iv = invs.get(p.asin)
        ad_profit = _product_ad_profit_metrics(p)
        items.append({
            "asin": p.asin,
            "name": p.product_name,
            "life_cycle": p.life_cycle,
            "product_type": p.product_type,
            "festival": p.festival,
            "box_qty": p.box_quantity,
            "calc_date": str(c.calc_date) if c else None,
            "score": c.purchase_score if c else None,
            "calc_level": c.purchase_level if c else None,
            "suggested_qty": c.suggested_qty if c else None,
            "inv_days": c.inventory_days if c else None,
            "replenish_cycle": c.replenishment_cycle if c else None,
            "trigger": c.purchase_trigger if c else None,
            "vol30": (sn.thirty_volume if sn else None),
            "yesterday_vol": (sn.yesterday_volume if sn else None),
            "avail": (iv.fba_available if iv else None),
            **ad_profit,
        })

    def safe_sum(key, func_, default=0):
        vals = [i[key] for i in items if i[key] is not None]
        return func_(vals) if vals else default

    scored = [i for i in items if i["score"] is not None]
    stockout = [i for i in items if i["inv_days"] is not None and i["replenish_cycle"] is not None
                and i["inv_days"] < i["replenish_cycle"]]
    overstock = sorted([i for i in items if (i["inv_days"] or 0) > 90], key=lambda x: -x["inv_days"])
    days0 = [i for i in items if i["inv_days"] == 0]

    top_sales = sorted([i for i in items if i["vol30"]], key=lambda x: -x["vol30"])[:10]
    return {
        "level": level,
        "snapshot_date": str(snap_date) if snap_date else None,
        "inventory_date": str(inv_date) if inv_date else None,
        "total": len(items),
        "overview": {
            "vol30_total": safe_sum("vol30", sum),
            "vol30_avg": round(safe_sum("vol30", sum) / max(len(items), 1)),
            "scored_count": len(scored),
            "avg_score": round(sum(i["score"] for i in scored) / len(scored), 1) if scored else None,
            "stockout_risk": len(stockout),
            "days0": len(days0),
            "overstock": len(overstock),
        },
        "distributions": {
            "life_cycle": dict(Counter(i["life_cycle"] or "未知" for i in items)),
            "product_type": dict(Counter(i["product_type"] or "未知" for i in items)),
            "festival": dict(Counter(i["festival"] or "无" for i in items).most_common(10)),
            "calc_level": dict(Counter(i["calc_level"] or "未触发" for i in items)),
        },
        "top_sales": top_sales,
        "risks": {
            "stockout": sorted(stockout, key=lambda x: x["inv_days"])[:20],
            "overstock": overstock[:20],
        },
        "items": items,
    }
