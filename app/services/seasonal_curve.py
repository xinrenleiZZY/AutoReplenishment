"""季节销售曲线模型

功能：
1. build_seasonal_curve - 构建季节销售曲线
2. get_curve - 获取已保存的季节曲线
3. predict_by_curve - 用季节曲线预测各月销量
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.sales import SalesData
from app.models.seasonal_curve import SeasonalCurve
from app.utils.helpers import safe_json_dumps

logger = logging.getLogger(__name__)

# 装饰品售卖截止天数（节日前）
DECORATION_SELLING_END_DAYS = 12  # 10-14天，取中间值
# 非装饰品售卖截止天数（节日前）
NON_DECORATION_SELLING_END_DAYS = 3

# 节日日期映射（月, 日）- 主要针对美国市场
# 可扩展，实际应从配置或数据库中读取
FESTIVAL_DATES: Dict[str, tuple] = {
    "新年": (1, 1),
    "情人节": (2, 14),
    "复活节": (3, 31),  # 近似值，实际每年浮动
    "母亲节": (5, 12),  # 5月第二个周日（近似）
    "父亲节": (6, 16),  # 6月第三个周日（近似）
    "独立日": (7, 4),
    "万圣节": (10, 31),
    "感恩节": (11, 28),  # 11月第四个周四（近似）
    "黑五网一": (11, 29),  # 感恩节后一天
    "圣诞节": (12, 25),
    "PrimeDay": (7, 15),  # 近似值，每年浮动
}


def _get_festival_date(festival: str, year: int) -> Optional[date]:
    """获取指定年份的节日日期

    Args:
        festival: 节日名称
        year: 年份

    Returns:
        date对象，如果节日不在映射表中则返回None
    """
    month_day = FESTIVAL_DATES.get(festival)
    if not month_day:
        logger.warning(f"节日 {festival} 未在映射表中，使用默认日期")
        return None
    return date(year, month_day[0], month_day[1])


async def build_seasonal_curve(
    festival: str,
    sub_category: str,
    sample_asins: List[str],
    session: AsyncSession,
) -> dict:
    """构建季节销售曲线

    筛选条件：同节日、同类型（装饰品/非装饰品）、成熟产品、≥4个ASIN。
    计算各月销售占比并存储到 SeasonalCurve 表。

    Args:
        festival: 节日名称
        sub_category: 子分类（装饰品/非装饰品）
        sample_asins: 样本ASIN列表（成熟产品）
        session: 数据库会话

    Returns:
        dict: {
            "festival": str,
            "sub_category": str,
            "month_distribution": dict,  # {"1": 0.05, "2": 0.10, ...}
            "sample_count": int,
            "sample_asins": list,
        }
    """
    if len(sample_asins) < 4:
        raise ValueError(f"样本ASIN数量不足，需要至少4个，当前{len(sample_asins)}个")

    # 验证节日和子分类
    if sub_category not in ("装饰品", "非装饰品"):
        raise ValueError(f"子分类必须为'装饰品'或'非装饰品'，当前: {sub_category}")

    # 获取当前年份和上一年
    today = date.today()
    current_year = today.year

    # 查询所有样本ASIN的历史月销量
    # 使用近两年的数据（当前年和上一年）
    monthly_sales: Dict[str, int] = {}
    for month in range(1, 13):
        monthly_sales[str(month)] = 0

    for asin in sample_asins:
        for year in [current_year - 1, current_year]:
            stmt = (
                select(
                    func.extract("month", SalesData.date).label("month"),
                    func.coalesce(func.sum(SalesData.sales_qty), 0).label("total"),
                )
                .where(
                    SalesData.asin == asin,
                    func.extract("year", SalesData.date) == year,
                )
                .group_by(func.extract("month", SalesData.date))
            )
            result = await session.execute(stmt)
            for row in result.all():
                m = str(int(row.month))
                monthly_sales[m] = monthly_sales.get(m, 0) + int(row.total)

    total_sales = sum(monthly_sales.values())

    if total_sales == 0:
        raise ValueError(f"节日 {festival} 的样本ASIN在近两年无销量数据")

    # 计算各月销售占比
    month_distribution: Dict[str, float] = {}
    for month in range(1, 13):
        m = str(month)
        month_distribution[m] = round(monthly_sales[m] / total_sales, 4)

    # 检查是否已有曲线记录
    stmt_existing = select(SeasonalCurve).where(
        SeasonalCurve.festival == festival,
        SeasonalCurve.sub_category == sub_category,
    )
    existing = await session.execute(stmt_existing)
    curve = existing.scalar_one_or_none()

    distribution_json = safe_json_dumps(month_distribution)
    asins_json = safe_json_dumps(sample_asins)

    if curve:
        curve.month_distribution = distribution_json
        curve.sample_count = len(sample_asins)
        curve.sample_asins = asins_json
    else:
        curve = SeasonalCurve(
            festival=festival,
            sub_category=sub_category,
            month_distribution=distribution_json,
            sample_count=len(sample_asins),
            sample_asins=asins_json,
        )
        session.add(curve)

    await session.commit()
    await session.refresh(curve)

    logger.info(
        f"季节曲线已保存: {festival}/{sub_category}, "
        f"样本数={curve.sample_count}"
    )

    return {
        "festival": curve.festival,
        "sub_category": curve.sub_category,
        "month_distribution": month_distribution,
        "sample_count": curve.sample_count,
        "sample_asins": sample_asins,
    }


async def get_curve(
    festival: str, sub_category: str, session: AsyncSession
) -> Optional[dict]:
    """获取已保存的季节曲线

    Args:
        festival: 节日名称
        sub_category: 子分类（装饰品/非装饰品）
        session: 数据库会话

    Returns:
        Optional[dict]: {
            "festival": str,
            "sub_category": str,
            "month_distribution": dict,
            "sample_count": int,
            "sample_asins": list,
        } 或 None
    """
    stmt = select(SeasonalCurve).where(
        SeasonalCurve.festival == festival,
        SeasonalCurve.sub_category == sub_category,
    )
    result = await session.execute(stmt)
    curve = result.scalar_one_or_none()

    if not curve:
        logger.info(f"未找到季节曲线: {festival}/{sub_category}")
        return None

    month_distribution = json.loads(curve.month_distribution)
    sample_asins = (
        json.loads(curve.sample_asins) if curve.sample_asins else []
    )

    return {
        "festival": curve.festival,
        "sub_category": curve.sub_category,
        "month_distribution": month_distribution,
        "sample_count": curve.sample_count,
        "sample_asins": sample_asins,
    }


async def predict_by_curve(
    actual_monthly_sales: Dict[str, int],
    curve: dict,
    session: AsyncSession,
) -> dict:
    """用季节曲线预测各月销量

    逻辑：
    1. 用已有实际销量反推全年预计总销量
    2. 按曲线占比分配后续月份销量
    3. 装饰品类：售卖期在节日前10-14天结束
    4. 非装饰品：售卖期在节日前3天结束

    Args:
        actual_monthly_sales: 已有实际月销量 {"1": 100, "2": 200, ...}
        curve: 季节曲线 dict（来自 get_curve 返回值）
        session: 数据库会话

    Returns:
        dict: {
            "festival": str,
            "sub_category": str,
            "estimated_annual_total": float,     # 预估年总销量
            "monthly_forecast": {                # 各月预测销量
                "1": {"predicted": float, "actual": Optional[int], "proportion": float},
                ...
            },
            "selling_end_date": Optional[str],   # 售卖截止日期
            "has_ended": bool,                   # 是否已过售卖截止期
        }
    """
    festival = curve["festival"]
    sub_category = curve["sub_category"]
    month_distribution = curve["month_distribution"]

    # 获取已有实际销量的月份
    actual_total = sum(actual_monthly_sales.values())

    if actual_total == 0:
        # 无实际数据，直接用曲线占比推算（假设年总量为100%基准）
        estimated_annual = 0
        logger.warning(
            f"无实际销量数据，无法反推年总量: {festival}/{sub_category}"
        )
        return {
            "festival": festival,
            "sub_category": sub_category,
            "estimated_annual_total": 0,
            "monthly_forecast": {},
            "selling_end_date": None,
            "has_ended": False,
        }

    # 计算已有实际销量的月份对应的曲线占比之和
    actual_proportion_sum = 0.0
    for month_str, qty in actual_monthly_sales.items():
        if qty > 0 and month_str in month_distribution:
            actual_proportion_sum += month_distribution[month_str]

    # 反推全年预计总销量
    if actual_proportion_sum > 0:
        estimated_annual_total = round(actual_total / actual_proportion_sum, 2)
    else:
        estimated_annual_total = 0

    # 计算各月预测销量
    monthly_forecast: Dict[str, dict] = {}
    for month in range(1, 13):
        m = str(month)
        proportion = month_distribution.get(m, 0)
        predicted = round(estimated_annual_total * proportion, 2)
        actual = actual_monthly_sales.get(m)

        monthly_forecast[m] = {
            "predicted": predicted,
            "actual": actual,
            "proportion": proportion,
        }

    # 计算售卖截止日期
    today = date.today()
    festival_date = _get_festival_date(festival, today.year)

    selling_end_date = None
    has_ended = False

    if festival_date:
        if sub_category == "装饰品":
            end_date = festival_date - timedelta(days=DECORATION_SELLING_END_DAYS)
        else:
            end_date = festival_date - timedelta(days=NON_DECORATION_SELLING_END_DAYS)

        selling_end_date = end_date.isoformat()
        has_ended = today >= end_date

        logger.info(
            f"节日 {festival} 日期: {festival_date}, "
            f"售卖截止日: {end_date}, "
            f"已截止: {has_ended}"
        )

    return {
        "festival": festival,
        "sub_category": sub_category,
        "estimated_annual_total": estimated_annual_total,
        "monthly_forecast": monthly_forecast,
        "selling_end_date": selling_end_date,
        "has_ended": has_ended,
    }
