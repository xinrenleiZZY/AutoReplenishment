"""S级/A级商品数据分析：档案 + 最近结果 + 销量快照 + 库存，输出 /tmp/sa_analysis.json 与汇总"""

import asyncio
import json
from collections import Counter

from sqlalchemy import select, func

from app.database import async_session_factory
from app.models.calculation import CalculationResult
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.inventory import InventorySnapshot
from app.models.product import Product


async def main():
    async with async_session_factory() as s:
        prods = (await s.execute(
            select(Product).where(Product.product_level.in_(["S", "A"]))
        )).scalars().all()
        asins = [p.asin for p in prods]

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
        calcs = {r.asin: r for r in (await s.execute(q)).scalars().all()}

        snap_date = (await s.execute(select(func.max(DailySalesSnapshot.snapshot_date)))).scalar()
        snaps = {}
        if snap_date:
            for r in (await s.execute(select(DailySalesSnapshot).where(
                    DailySalesSnapshot.snapshot_date == snap_date,
                    DailySalesSnapshot.asin.in_(asins)))).scalars().all():
                snaps[r.asin] = r

        inv_date = (await s.execute(select(func.max(InventorySnapshot.snapshot_date)))).scalar()
        invs = {}
        if inv_date:
            for r in (await s.execute(select(InventorySnapshot).where(
                    InventorySnapshot.snapshot_date == inv_date,
                    InventorySnapshot.asin.in_(asins)))).scalars().all():
                invs[r.asin] = r

        out = []
        for p in prods:
            c = calcs.get(p.asin)
            sn = snaps.get(p.asin)
            iv = invs.get(p.asin)
            out.append({
                "asin": p.asin, "name": p.product_name, "level": p.product_level,
                "status": p.status, "life_cycle": p.life_cycle, "product_type": p.product_type,
                "festival": p.festival, "core_months": p.core_months, "category": p.category,
                "brand": p.brand, "shop": p.shop, "price": p.price, "box_qty": p.box_quantity,
                "list_date": str(p.list_date) if p.list_date else None, "operator": p.operator,
                "calc_date": str(c.calc_date) if c else None,
                "score": c.purchase_score if c else None, "calc_level": c.purchase_level if c else None,
                "suggested_qty": c.suggested_qty if c else None, "inv_days": c.inventory_days if c else None,
                "replenish_cycle": c.replenishment_cycle if c else None, "trigger": c.purchase_trigger if c else None,
                "snap_date": str(sn.snapshot_date) if sn else None,
                "yesterday_vol": sn.yesterday_volume if sn else None,
                "seven_vol": sn.seven_volume if sn else None,
                "fourteen_vol": sn.fourteen_volume if sn else None,
                "thirty_vol": sn.thirty_volume if sn else None,
                "inv_available": iv.fba_available if iv else None,
                "inv_date": str(iv.snapshot_date) if iv else None,
            })

        with open("/tmp/sa_analysis.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)

        # 汇总
        by_level = Counter(p.product_level for p in prods)
        by_status = Counter(p.status for p in prods)
        by_life = Counter(p.life_cycle for p in prods)
        by_type = Counter(p.product_type for p in prods)
        by_festival = Counter((p.festival or "无") for p in prods)
        by_calc = Counter((c.purchase_level if (c := calcs.get(p.asin)) else "无结果") for p in prods)
        active = [p for p in prods if p.status]
        print("TOTAL:", len(prods), "| ACTIVE:", len(active))
        print("LEVEL:", dict(by_level))
        print("STATUS:", dict(by_status))
        print("LIFECYCLE:", dict(by_life))
        print("TYPE:", dict(by_type))
        print("CALC_LEVEL:", dict(by_calc))
        print("FESTIVAL_TOP:", dict(by_festival.most_common(10)))
        print("SNAP_DATE:", snap_date, "| INV_DATE:", inv_date, "| SNAP_COUNT:", len(snaps), "| INV_COUNT:", len(invs), "| CALC_COUNT:", len(calcs))
        # 销量/库存汇总（活跃产品）
        vols = [(p.asin, snaps.get(p.asin).thirty_volume or 0) for p in active if snaps.get(p.asin)]
        vols.sort(key=lambda x: -x[1])
        print("TOP30_DAYS:", [(a, v) for a, v in vols[:10]])
        risk = [(p.asin, calcs[p.asin].inventory_days, calcs[p.asin].replenishment_cycle)
                for p in active if p.asin in calcs and calcs[p.asin].inventory_days is not None
                and calcs[p.asin].replenishment_cycle is not None
                and calcs[p.asin].inventory_days < calcs[p.asin].replenishment_cycle]
        print("STOCKOUT_RISK_COUNT:", len(risk), "| SAMPLE:", risk[:8])
        over = [(p.asin, calcs[p.asin].inventory_days) for p in active if p.asin in calcs
                and calcs[p.asin].inventory_days is not None and calcs[p.asin].inventory_days > 90]
        print("OVERSTOCK_COUNT:", len(over), "| SAMPLE:", over[:8])


if __name__ == "__main__":
    asyncio.run(main())
