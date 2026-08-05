# -*- coding: utf-8 -*-
"""真实 ASIN 端到端验证 run_single_calculation

用法: python scripts/verify_run_single.py
"""
import asyncio
import json
import sys

sys.path.insert(0, r"e:\ZY2026\yy021-自动补货决策系统")

from sqlalchemy import select

from app.database import async_session_factory
from app.models.product import Product
from app.tasks.calculation_tasks import run_single_calculation


async def main():
    s = async_session_factory()
    async with s:
        asin = (await s.execute(
            select(Product.asin).where(Product.festival.isnot(None)).limit(1)
        )).scalars().first()
    await s.close()
    if not asin:
        print("无节日产品")
        return
    print(f"真实计算 ASIN={asin}")

    s2 = async_session_factory()
    async with s2:
        r = await run_single_calculation(asin, s2)
    await s2.close()

    if "error" in r:
        print("error:", r["error"])
        return
    print(f"产品: {r['product_name'][:40]}")
    print(f"生命周期: {r['life_cycle']}")
    print(f"预测总量: {r['forecast_total']}  库存: {r['available_stock']}")
    print(f"触发: {r['purchase_trigger']}  覆盖{r['inventory_days']}天")
    print(f"建议量: {r['suggested_qty']}  批次: {json.dumps(r['batch_plan'], ensure_ascii=False)}")
    print(f"评分: {r['purchase_score']}  等级: {r['purchase_level']}")
    for st in r["steps"]:
        if st["step_no"] in (7, 8, 9, 10, 11):
            print(f"  Step{st['step_no']} {st['step_name']}: {st['reason']}")


asyncio.run(main())
