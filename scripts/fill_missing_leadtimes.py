# -*- coding: utf-8 -*-
"""补充缺失分类的工期数据到 category_leadtimes 表

缺失分类: 灯串类/珠串类/岩石类/水圈类/五金类/解压玩具类/毛毡类
统一工期: 15-20 天

用法: python scripts/fill_missing_leadtimes.py
"""
import asyncio
import sys

sys.path.insert(0, r"e:\ZY2026\yy021-自动补货决策系统")

from sqlalchemy import select
from app.database import async_session_factory
from app.models.category_leadtime import CategoryLeadtime

# 缺失分类 → (min, max, 备注)
MISSING = {
    "灯串类": (15, 20, "补充工期(统一默认)"),
    "珠串类": (15, 20, "补充工期(统一默认)"),
    "岩石类": (15, 20, "补充工期(统一默认)"),
    "水圈类": (15, 20, "补充工期(统一默认)"),
    "五金类": (15, 20, "补充工期(统一默认)"),
    "解压玩具类": (15, 20, "补充工期(统一默认)"),
    "毛毡类": (15, 20, "补充工期(统一默认)"),
}


async def main():
    session = async_session_factory()
    try:
        async with session:
            # 查已有 L1 分类
            existing = set((await session.execute(
                select(CategoryLeadtime.level1_category).distinct()
            )).scalars().all())
            print(f"已有 L1 分类: {len(existing)} 个")

            added = 0
            for cat, (lo, hi, note) in MISSING.items():
                if cat in existing:
                    print(f"  跳过(已存在): {cat}")
                    continue
                session.add(CategoryLeadtime(
                    level1_category=cat,
                    level2_category=None,
                    lead_time_min=lo,
                    lead_time_max=hi,
                    notes=note,
                ))
                added += 1
                print(f"  新增: {cat} min={lo} max={hi}")

            await session.commit()
            print(f"\n完成: 新增 {added} 个分类工期")

            # 验证匹配率
            from app.models.product import Product
            from sqlalchemy import func
            p_cats = (await session.execute(
                select(Product.category, func.count(Product.asin))
                .where(Product.category.isnot(None))
                .group_by(Product.category)
            )).all()
            new_existing = set((await session.execute(
                select(CategoryLeadtime.level1_category).distinct()
            )).scalars().all())
            missing = [c for c, _ in p_cats if c not in new_existing]
            print(f"产品分类 {len(p_cats)} 个, 工期表 L1 {len(new_existing)} 个, 仍缺失: {missing or '无'}")
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
