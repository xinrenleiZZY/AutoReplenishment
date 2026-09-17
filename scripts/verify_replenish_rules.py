# -*- coding: utf-8 -*-
"""验证补货周期/箱规/最低采购量/评分规则的实现

用法: python scripts/verify_replenish_rules.py
"""
import asyncio
import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from sqlalchemy import select

from app.database import async_session_factory
from app.models.product import Product
from app.models.category_leadtime import CategoryLeadtime
from app.tasks.calculation_tasks import (
    _resolve_lead_time,
    _calc_purchase_trigger,
    _calc_suggested_qty,
    _plan_batches,
    _calc_score,
)


async def main():
    s = async_session_factory()
    async with s:
        # 1. 分类工期匹配
        lt = await _resolve_lead_time(Product(asin="T1", product_name="测试", category="PVC类"), s)
        print(f"1. PVC类 匹配工期: {lt} 天 (工期表15-20)")
        lt2 = await _resolve_lead_time(Product(asin="T2", product_name="测试", category="不存在分类"), s)
        print(f"   未知分类兜底: {lt2} 天 (期望30)")
        lt3 = await _resolve_lead_time(Product(asin="T3", product_name="测试", category="PVC类", lead_time=25), s)
        print(f"   产品实际填写优先: {lt3} 天 (期望25)")

        # 产品分类 vs 工期表 L1 匹配率
        p_cats = (await s.execute(
            select(Product.category).where(Product.category.isnot(None)).distinct()
        )).scalars().all()
        t_cats = (await s.execute(
            select(CategoryLeadtime.level1_category).distinct()
        )).scalars().all()
        matched = [c for c in p_cats if c in t_cats]
        print(f"   产品分类{len(p_cats)}个, 工期表L1{len(t_cats)}个, 匹配{len(matched)}个: {matched}")
    await s.close()
    print()

    peak = 8 <= date.today().month <= 12
    print(f"2. 当前月份 {date.today().month} -> {'旺季' if peak else '淡季'} (运输: 海运45/空派15/快递6)")

    # 2. 触发判断
    forecast = {
        "forecast_total": 25000,
        "forecast_months": [
            {"forecast_qty": 8000}, {"forecast_qty": 9000}, {"forecast_qty": 8000},
            {"forecast_qty": 0}, {"forecast_qty": 0}, {"forecast_qty": 0},
        ],
    }
    inv = {"available_stock": 15000, "thirty_volume": 9000}
    tr = _calc_purchase_trigger(forecast, inv, Product(asin="T", product_name="t", category="PVC类"), 18)
    print(f"   触发={tr['purchase_trigger']} 覆盖{tr['inventory_days']}天 周期{tr['transport_cycles']} 推荐={tr['recommended_transport']}")
    print(f"   reason: {tr['reason']}")

    # 3. 建议量: 3个月需求25000 - 库存15000 = 10000 -> 34箱
    p_box = Product(asin="T", product_name="t", category="PVC类", box_quantity=300)
    qty = _calc_suggested_qty(forecast, inv, p_box)
    print(f"3. 建议量: {qty} (期望 34*300=10200)")

    # 4. 批次
    print(f"4. 批次: {_plan_batches(qty, p_box, 18)}")

    # 5. 评分（断货风险25%+销量趋势25%+利润20%+生命10%+库存10%+运输10%）
    scoring = _calc_score(
        forecast, inv, "热卖期", tr,
        Product(asin="T", product_name="t", category="PVC类", profit_rate=0.3),
        {"can_purchase": True, "recommended_transport": "海运"},
    )
    print(f"5. 评分={scoring['purchase_score']} 等级={scoring['purchase_level']}")
    for k, v in scoring["score_detail"].items():
        print(f"   {v['label']}: {v['value']}分 x {v['weight']}")


asyncio.run(main())
