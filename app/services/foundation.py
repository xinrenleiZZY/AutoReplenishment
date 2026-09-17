# -*- coding: utf-8 -*-
"""基础数据刷新（每日计算前执行）

顺序严格按依赖：新老品判定 → SABCD 等级 → 节日/产品类型 → 生命周期（节日时间点表优先）。
避免新同步产品带着空基础字段进入计算导致分数失真。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product

logger = logging.getLogger(__name__)


async def refresh_foundation(session: AsyncSession, write: bool = True) -> dict:
    """刷新全部启用产品的基础字段并提交。返回各步骤统计。"""
    from scripts.assign_product_levels import run_assign_levels
    from scripts.assign_festival_from_tags import run_assign_festival
    from app.services.festival_lifecycle import lifecycle_by_festival, resolve_festival

    # 1) 新老品（product_stage）+ SABCD 等级（product_level/calc_frequency）
    level_stats = await run_assign_levels(session, dry_run=not write)
    # 2) 节日 + 产品类型（listing 标签）
    fest_stats = await run_assign_festival(session, dry_run=not write)
    # 3) 生命周期：节日时间点表优先写库（匹配不到保留原值/SIF 值）
    products = (await session.execute(
        select(Product).where(Product.status == True)  # noqa: E712
    )).scalars().all()
    lifecycle_updated = 0
    for p in products:
        # 长期产品：全年销售不过季，固定默认热卖期（与计算时 _identify_lifecycle 一致）
        if (getattr(p, "product_type", "") or "") == "长期产品":
            if p.life_cycle != "热卖期":
                if write:
                    p.life_cycle = "热卖期"
                lifecycle_updated += 1
            continue
        # 非节日产品（festival 为空且非长期产品）：标签缺少导致无分类，保持原值
        if not await resolve_festival(p, session):
            continue
        fc = await lifecycle_by_festival(p, session)
        if fc is None:
            # 节日产品当前日期不在任何阶段区间 → 下降期
            fc = "下降期"
        if fc and p.life_cycle != fc:
            if write:
                p.life_cycle = fc
            lifecycle_updated += 1
    if write:
        await session.commit()
    logger.info("基础数据刷新完成: levels=%s festivals=%s lifecycle_updated=%s",
                level_stats, fest_stats, lifecycle_updated)
    return {
        "total": len(products),
        "levels": level_stats,
        "festivals": fest_stats,
        "lifecycle_updated": lifecycle_updated,
    }
