"""历史销量分析模块

功能：
1. analyze_monthly_sales - 获取某年各月销量汇总
2. calculate_yoy_growth - 计算同比增长率
3. calculate_mom_growth - 计算环比增长率
4. analyze_sales_trend - 分析7天/14天/30天销量趋势
5. get_peak_months - 判断旺季/淡季月份
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sales import SalesData

logger = logging.getLogger(__name__)

# 趋势判断阈值
TREND_HIGH_GROWTH = 0.30       # 增长≥30%
TREND_MODERATE_GROWTH = 0.10   # 增长10-30%
TREND_MODERATE_DECLINE = -0.10  # 下降10-30%
TREND_HIGH_DECLINE = -0.30     # 下降≥30%

# 旺季判断阈值（月均销量的倍数）
PEAK_MONTH_THRESHOLD = 1.5
OFF_PEAK_MONTH_THRESHOLD = 0.5


async def analyze_monthly_sales(asin: str, year: int, session: AsyncSession) -> dict:
    """获取某年各月销量汇总

    Args:
        asin: ASIN编码
        year: 年份
        session: 数据库会话

    Returns:
        dict: {
            "asin": str,
            "year": int,
            "monthly_data": {
                "1": {"total_qty": int, "avg_daily_qty": float},
                ...
            },
            "total_year": int,
        }
    """
    # 查询各月销量汇总
    stmt = (
        select(
            func.extract("month", SalesData.date).label("month"),
            func.coalesce(func.sum(SalesData.sales_qty), 0).label("total_qty"),
            func.coalesce(func.avg(SalesData.sales_qty), 0).label("avg_daily_qty"),
        )
        .where(
            SalesData.asin == asin,
            func.extract("year", SalesData.date) == year,
        )
        .group_by(func.extract("month", SalesData.date))
        .order_by(func.extract("month", SalesData.date))
    )

    result = await session.execute(stmt)
    rows = result.all()

    monthly_data: Dict[str, dict] = {}
    total_year = 0

    for row in rows:
        month = str(int(row.month))
        total = int(row.total_qty)
        monthly_data[month] = {
            "total_qty": total,
            "avg_daily_qty": round(float(row.avg_daily_qty), 2),
        }
        total_year += total

    return {
        "asin": asin,
        "year": year,
        "monthly_data": monthly_data,
        "total_year": total_year,
    }


async def calculate_yoy_growth(
    asin: str, current_year: int, session: AsyncSession
) -> dict:
    """计算同比增长率

    对比当年与上一年同期的月销量增长率。

    Args:
        asin: ASIN编码
        current_year: 当前年份
        session: 数据库会话

    Returns:
        dict: {
            "asin": str,
            "current_year": int,
            "previous_year": int,
            "monthly_yoy": {
                "1": {"current": int, "previous": int, "growth_rate": float},
                ...
            },
            "overall_growth_rate": float,
        }
    """
    previous_year = current_year - 1

    # 查询当年各月销量
    current_stmt = (
        select(
            func.extract("month", SalesData.date).label("month"),
            func.coalesce(func.sum(SalesData.sales_qty), 0).label("total_qty"),
        )
        .where(
            SalesData.asin == asin,
            func.extract("year", SalesData.date) == current_year,
        )
        .group_by(func.extract("month", SalesData.date))
    )
    current_result = await session.execute(current_stmt)
    current_rows = {int(r.month): int(r.total_qty) for r in current_result.all()}

    # 查询上一年各月销量
    prev_stmt = (
        select(
            func.extract("month", SalesData.date).label("month"),
            func.coalesce(func.sum(SalesData.sales_qty), 0).label("total_qty"),
        )
        .where(
            SalesData.asin == asin,
            func.extract("year", SalesData.date) == previous_year,
        )
        .group_by(func.extract("month", SalesData.date))
    )
    prev_result = await session.execute(prev_stmt)
    prev_rows = {int(r.month): int(r.total_qty) for r in prev_result.all()}

    # 计算各月同比增长率
    monthly_yoy: Dict[str, dict] = {}
    total_current = 0
    total_previous = 0

    for month in range(1, 13):
        c = current_rows.get(month, 0)
        p = prev_rows.get(month, 0)
        total_current += c
        total_previous += p

        growth_rate = 0.0
        if p > 0:
            growth_rate = round((c - p) / p, 4)

        monthly_yoy[str(month)] = {
            "current": c,
            "previous": p,
            "growth_rate": growth_rate,
        }

    overall_growth_rate = 0.0
    if total_previous > 0:
        overall_growth_rate = round(
            (total_current - total_previous) / total_previous, 4
        )

    return {
        "asin": asin,
        "current_year": current_year,
        "previous_year": previous_year,
        "monthly_yoy": monthly_yoy,
        "overall_growth_rate": overall_growth_rate,
    }


async def calculate_mom_growth(
    asin: str, months: int = 30, session: Optional[AsyncSession] = None
) -> dict:
    """计算环比增长率

    对比最近一个完整月份与上一个月的销量增长率。
    使用 months 参数表示取最近多少天的数据来判定"最近月份"。

    Args:
        asin: ASIN编码
        months: 用于判断最近周期的天数（默认30天）
        session: 数据库会话

    Returns:
        dict: {
            "asin": str,
            "current_period_sales": int,
            "previous_period_sales": int,
            "growth_rate": float,
            "current_period_days": int,
            "previous_period_days": int,
        }
    """
    today = date.today()

    # 当前时间段
    current_start = today - timedelta(days=months)
    # 上一时间段（同长度）
    previous_start = current_start - timedelta(days=months)

    # 当前周期销量
    current_stmt = (
        select(func.coalesce(func.sum(SalesData.sales_qty), 0))
        .where(
            SalesData.asin == asin,
            SalesData.date >= current_start,
            SalesData.date < today,
        )
    )
    current_result = await session.execute(current_stmt)
    current_sales = current_result.scalar() or 0

    # 上一周期销量
    prev_stmt = (
        select(func.coalesce(func.sum(SalesData.sales_qty), 0))
        .where(
            SalesData.asin == asin,
            SalesData.date >= previous_start,
            SalesData.date < current_start,
        )
    )
    prev_result = await session.execute(prev_stmt)
    prev_sales = prev_result.scalar() or 0

    growth_rate = 0.0
    if prev_sales > 0:
        growth_rate = round((current_sales - prev_sales) / prev_sales, 4)

    return {
        "asin": asin,
        "current_period_sales": current_sales,
        "previous_period_sales": prev_sales,
        "growth_rate": growth_rate,
        "current_period_days": months,
        "previous_period_days": months,
    }


async def analyze_sales_trend(asin: str, session: AsyncSession) -> dict:
    """分析7天/14天/30天销量趋势

    计算三个时间窗口的日均销量及环比变化，返回增长趋势描述。

    Args:
        asin: ASIN编码
        session: 数据库会话

    Returns:
        dict: {
            "asin": str,
            "trends": {
                "7d": {"avg_daily": float, "growth_rate": float, "trend_desc": str},
                "14d": {"avg_daily": float, "growth_rate": float, "trend_desc": str},
                "30d": {"avg_daily": float, "growth_rate": float, "trend_desc": str},
            },
        }
    """
    today = date.today()

    trends = {}
    for window in [7, 14, 30]:
        window_start = today - timedelta(days=window)
        prev_window_start = window_start - timedelta(days=window)

        # 当前窗口销量
        current_stmt = (
            select(func.coalesce(func.sum(SalesData.sales_qty), 0))
            .where(
                SalesData.asin == asin,
                SalesData.date >= window_start,
                SalesData.date < today,
            )
        )
        current_result = await session.execute(current_stmt)
        current_total = current_result.scalar() or 0
        current_avg = round(current_total / window, 2)

        # 上一窗口销量
        prev_stmt = (
            select(func.coalesce(func.sum(SalesData.sales_qty), 0))
            .where(
                SalesData.asin == asin,
                SalesData.date >= prev_window_start,
                SalesData.date < window_start,
            )
        )
        prev_result = await session.execute(prev_stmt)
        prev_total = prev_result.scalar() or 0
        prev_avg = round(prev_total / window, 2)

        # 计算增长率
        growth_rate = 0.0
        if prev_avg > 0:
            growth_rate = round((current_avg - prev_avg) / prev_avg, 4)

        trends[f"{window}d"] = {
            "avg_daily": current_avg,
            "growth_rate": growth_rate,
            "trend_desc": _describe_trend(growth_rate),
        }

    return {
        "asin": asin,
        "trends": trends,
    }


def _describe_trend(growth_rate: float) -> str:
    """根据增长率返回趋势描述"""
    if growth_rate >= TREND_HIGH_GROWTH:
        return "高速增长（≥30%）"
    elif growth_rate >= TREND_MODERATE_GROWTH:
        return "稳步增长（10%-30%）"
    elif growth_rate >= TREND_MODERATE_DECLINE:
        return "基本稳定（±10%）"
    elif growth_rate >= TREND_HIGH_DECLINE:
        return "明显下降（10%-30%）"
    else:
        return "大幅下降（≥30%）"


async def get_peak_months(asin: str, year: int, session: AsyncSession) -> dict:
    """判断旺季/淡季月份

    基于月销量与月均销量的比值：
    - 旺季：月销量 > 月均销量 × 1.5
    - 淡季：月销量 < 月均销量 × 0.5
    - 正常：介于两者之间

    Args:
        asin: ASIN编码
        year: 年份
        session: 数据库会话

    Returns:
        dict: {
            "asin": str,
            "year": int,
            "peak_months": [str, ...],        # 旺季月份列表 ["1", "2", ...]
            "off_peak_months": [str, ...],     # 淡季月份列表
            "normal_months": [str, ...],       # 正常月份列表
            "monthly_ratios": {                # 各月与月均销量比值
                "1": {"total_qty": int, "ratio": float},
                ...
            },
        }
    """
    monthly_data = await analyze_monthly_sales(asin, year, session)
    monthly = monthly_data["monthly_data"]
    total_year = monthly_data["total_year"]

    if total_year == 0:
        return {
            "asin": asin,
            "year": year,
            "peak_months": [],
            "off_peak_months": [],
            "normal_months": [],
            "monthly_ratios": {},
        }

    monthly_avg = total_year / 12.0

    peak_months: List[str] = []
    off_peak_months: List[str] = []
    normal_months: List[str] = []
    monthly_ratios: Dict[str, dict] = {}

    for month_num in range(1, 13):
        m = str(month_num)
        if m in monthly:
            qty = monthly[m]["total_qty"]
            ratio = round(qty / monthly_avg, 4) if monthly_avg > 0 else 0
        else:
            qty = 0
            ratio = 0

        monthly_ratios[m] = {"total_qty": qty, "ratio": ratio}

        if ratio >= PEAK_MONTH_THRESHOLD:
            peak_months.append(m)
        elif ratio <= OFF_PEAK_MONTH_THRESHOLD:
            off_peak_months.append(m)
        else:
            normal_months.append(m)

    return {
        "asin": asin,
        "year": year,
        "peak_months": peak_months,
        "off_peak_months": off_peak_months,
        "normal_months": normal_months,
        "monthly_ratios": monthly_ratios,
    }
