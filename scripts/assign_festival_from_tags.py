# -*- coding: utf-8 -*-
"""节日字段按 listing 标签修正 + 节日/长期产品分类对齐（替代按产品名关键词猜测/ERP分类）

用法:
  python -m scripts.assign_festival_from_tags --dry-run   # 预览
  python -m scripts.assign_festival_from_tags             # 写库
"""
import asyncio
import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_session_factory
from app.models.product import Product
from app.services.tag_festival import classify_product, load_festival_maps, load_calendar_names


async def run_assign_festival(session, dry_run: bool = False) -> dict:
    """全量节日/长期分类对齐：节日（listing标签，长期特例优先）+ 产品类型。返回统计。"""
    from app.services.config_service import get_param

    long_tags = (await get_param(session, "product_type_long_tags")) or "长期,西部牛仔"
    fest_tags = (await get_param(session, "product_type_festival_tags")) or ""
    festival_map, season_map = await load_festival_maps(session)
    calendar_names = await load_calendar_names(session)
    products = (await session.execute(
        select(Product).where(Product.status == True)  # noqa: E712
    )).scalars().all()
    updated = 0
    changed = 0
    type_changed = 0
    for p in products:
        new, new_type = classify_product(p.tags, long_tags, fest_tags,
                                         festival_map, season_map, calendar_names)
        if new is not None:
            updated += 1
            if new != p.festival:
                if not dry_run:
                    p.festival = new
                changed += 1
        elif p.festival:
            if not dry_run:
                p.festival = None
            changed += 1
        if (p.product_type or "") != new_type:
            if not dry_run:
                p.product_type = new_type
            type_changed += 1
    if not dry_run:
        await session.commit()
    print(f"dry_run={dry_run} 在售={len(products)} 有标签节日={updated} 节日修正={changed} 类型修正={type_changed}")
    return {"total": len(products), "festival": updated, "festival_changed": changed, "type_changed": type_changed}


async def main(dry_run: bool):
    async with async_session_factory() as session:
        await run_assign_festival(session, dry_run)


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
