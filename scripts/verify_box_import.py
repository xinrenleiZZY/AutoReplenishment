# -*- coding: utf-8 -*-
"""验证箱规入库 + 工期匹配率"""
import asyncio
import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func

from app.database import async_session_factory
from app.models.product import Product
from app.models.category_leadtime import CategoryLeadtime


async def main():
    s = async_session_factory()
    async with s:
        filled = (await s.execute(
            select(func.count()).select_from(Product).where(Product.box_quantity.isnot(None))
        )).scalar()
        total = (await s.execute(select(func.count()).select_from(Product))).scalar()
        print(f"box_quantity 已填: {filled}/{total}")
        rows = (await s.execute(
            select(Product.asin, Product.box_quantity).where(Product.box_quantity.isnot(None)).limit(5)
        )).all()
        print(f"  样本: {[(r[0], r[1]) for r in rows]}")

        p_cats = set((await s.execute(
            select(Product.category).where(Product.category.isnot(None)).distinct()
        )).scalars().all())
        t_cats = set((await s.execute(
            select(CategoryLeadtime.level1_category).distinct()
        )).scalars().all())
        missing = [c for c in p_cats if c not in t_cats]
        print(f"工期分类: 产品{len(p_cats)} 表{len(t_cats)} 缺失{missing or '无'}")
    await s.close()


asyncio.run(main())
