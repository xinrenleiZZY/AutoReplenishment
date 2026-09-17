# -*- coding: utf-8 -*-
"""销量解析模块（统一复用）

数据源：daily_sales_stats 表（领星 sales-statistics/report/list，filterDateType=day 实抓的
真实逐日销量，自 2025-01-01 起可覆盖去年数据，仅存系统跟踪产品）。

使用原则（用户已确认：升级为优先/双源校验）：
  - daily_sales_stats 是逐日实抓的**完整真实数据**，作为销量分析的**优先源**；
  - 仅当某 ASIN 在某区间在 daily_sales_stats **无任何记录**（非跟踪产品/未覆盖）时，
    才回退 sales_data 明细；两者均有数据时以 daily_sales_stats 为准（更完整）。
  - 保留 *_fallback 系列函数供确需「原数据源优先」的老逻辑调用。
"""

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_sales_stat import DailySalesStat
from app.models.sales import SalesData

logger = logging.getLogger(__name__)


async def sum_daily_sales_fallback(
    asin: str, start: date, end: date, session: AsyncSession
) -> int | None:
    """从 daily_sales_stats 兜底求和某 ASIN 在 [start, end] 区间的销量。

    若该区间无任何逐日记录，返回 None（表示兜底源也无销量，调用方按 0 / 无数据处理）。

    Returns:
        int: 区间销量合计；None 表示该区间兜底源无记录。
    """
    if start > end:
        return None
    row = (await session.execute(
        select(func.coalesce(func.sum(DailySalesStat.sales_qty), 0))
        .where(
            DailySalesStat.asin == asin,
            DailySalesStat.stat_date >= start,
            DailySalesStat.stat_date <= end,
        )
    )).scalar_one()
    return int(row)


async def get_daily_sales_fallback(
    asin: str, start: date, end: date, session: AsyncSession, use_zero: bool = True
) -> dict[date, int]:
    """从 daily_sales_stats 兜底取某 ASIN 在 [start, end] 区间的逐日销量。

    Returns:
        dict: {统计日期: 单日销量}。若 use_zero=True，区间内无记录的天填充 0；
              否则只返回有记录的日期。默认填充 0 便于直接做日均/趋势计算。
    """
    if start > end:
        return {}
    rows = (await session.execute(
        select(DailySalesStat.stat_date, DailySalesStat.sales_qty)
        .where(
            DailySalesStat.asin == asin,
            DailySalesStat.stat_date >= start,
            DailySalesStat.stat_date <= end,
        )
        .order_by(DailySalesStat.stat_date)
    )).all()
    result: dict[date, int] = {r[0]: r[1] for r in rows}
    if use_zero:
        d = start
        while d <= end:
            result.setdefault(d, 0)
            d += timedelta(days=1)
    return result


# ────────────────────────────────────────────────────────────────
# 双源优先解析（daily_sales_stats 优先；区间内其无记录时回退 sales_data）
# ────────────────────────────────────────────────────────────────

async def sum_daily_sales_dual(
    asin: str, start: date, end: date, session: AsyncSession
) -> int | None:
    """双源求和：优先 daily_sales_stats（完整逐日实抓）；该区间无记录则回退 sales_data 求和。

    Returns:
        int: 区间销量合计；None 表示两源在该区间都无记录。
    """
    if start > end:
        return None
    stats_sum = await sum_daily_sales_fallback(asin, start, end, session)
    if stats_sum:
        return stats_sum  # daily_sales_stats 有数据 → 优先采用（更完整）
    row = (await session.execute(
        select(func.coalesce(func.sum(SalesData.sales_qty), 0))
        .where(SalesData.asin == asin, SalesData.date >= start, SalesData.date <= end)
    )).scalar_one()
    total = int(row)
    return total if total > 0 else None


async def get_daily_sales_dual(
    asin: str, start: date, end: date, session: AsyncSession, use_zero: bool = True
) -> dict[date, int]:
    """双源逐日：优先 daily_sales_stats；该区间无记录则回退 sales_data 明细。

    Returns:
        dict: {统计日期: 单日销量}。use_zero=True 时区间内无记录的天填充 0。
    """
    if start > end:
        return {}
    stats = await get_daily_sales_fallback(asin, start, end, session, use_zero=False)
    if stats:
        result = dict(stats)
    else:
        rows = (await session.execute(
            select(SalesData.date, SalesData.sales_qty)
            .where(SalesData.asin == asin, SalesData.date >= start, SalesData.date <= end)
            .order_by(SalesData.date)
        )).all()
        result = {r[0]: r[1] for r in rows}
    if use_zero:
        d = start
        while d <= end:
            result.setdefault(d, 0)
            d += timedelta(days=1)
    return result
