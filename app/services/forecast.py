"""
未来销量预测模块

提供老品和新品两种预测模型，以及综合预测未来N月功能。
"""

import logging
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sales import SalesData
from app.models.product import Product
from app.models.seasonal_curve import SeasonalCurve
from app.config import settings

logger = logging.getLogger(__name__)


async def old_product_forecast(
    asin: str,
    target_month: date,
    trend_coeff: float = 1.0,
    market_coeff: float = 1.0,
    ad_coeff: float = 1.0,
    listing_coeff: float = 1.0,
    session: Optional[AsyncSession] = None,
) -> int:
    """
    老品预测模型

    预测销量 = 历史同期销量 × 趋势修正系数(25%) × 市场修正系数(15%)
                         × 广告修正系数(10%) × Listing修正系数(10%)

    各系数的调整幅度由权重缩放:
      修正系数 = 1 + (系数值 - 1) × 权重

    Args:
        asin: ASIN编码
        target_month: 目标月份（取该月第一天）
        trend_coeff: 趋势系数（基于30天销量增长率）
        market_coeff: 市场系数（基于关键词排名+类目排名变化）
        ad_coeff: 广告系数（基于ACOS+广告订单占比）
        listing_coeff: Listing系数（基于评分+Review增长）
        session: 数据库会话

    Returns:
        预测销量（四舍五入取整）
    """
    # 权重
    TREND_WEIGHT = 0.25
    MARKET_WEIGHT = 0.15
    AD_WEIGHT = 0.10
    LISTING_WEIGHT = 0.10

    # 查询历史同期销量（去年同月）
    last_year = target_month.year - 1
    last_year_start = date(last_year, target_month.month, 1)

    # 计算该月的天数
    if target_month.month == 12:
        next_month = date(target_month.year + 1, 1, 1)
    else:
        next_month = date(target_month.year, target_month.month + 1, 1)
    month_end = date(last_year, next_month.month, 1) if next_month.month > 1 else date(last_year + 1, 1, 1)
    from datetime import timedelta
    last_year_end = month_end - timedelta(days=1)

    historical_sales = 0
    if session is not None:
        query = (
            select(func.coalesce(func.sum(SalesData.sales_qty), 0))
            .where(
                SalesData.asin == asin,
                SalesData.date >= last_year_start,
                SalesData.date <= last_year_end,
            )
        )
        result = await session.execute(query)
        historical_sales = result.scalar() or 0

    if historical_sales == 0:
        logger.warning(f"[{asin}] 去年同月({last_year_start})无历史销量数据")
        return 0

    # 计算综合修正系数
    # 每个系数围绕1.0波动，乘以权重后累加
    adjustment = (
        1.0
        + (trend_coeff - 1.0) * TREND_WEIGHT
        + (market_coeff - 1.0) * MARKET_WEIGHT
        + (ad_coeff - 1.0) * AD_WEIGHT
        + (listing_coeff - 1.0) * LISTING_WEIGHT
    )

    forecast = round(historical_sales * adjustment)
    logger.info(
        f"[{asin}] 老品预测: 历史销量={historical_sales}, "
        f"趋势={trend_coeff:.4f}, 市场={market_coeff:.4f}, "
        f"广告={ad_coeff:.4f}, Listing={listing_coeff:.4f}, "
        f"修正系数={adjustment:.4f}, 预测={forecast}"
    )
    return forecast


async def new_product_forecast(
    asin: str,
    target_month: date,
    annual_forecast: Optional[int] = None,
    actual_sales_7d: int = 0,
    trend_coeff: float = 1.0,
    ad_coeff: float = 1.0,
    listing_coeff: float = 1.0,
    session: Optional[AsyncSession] = None,
) -> int:
    """
    新品预测模型

    预测销量 = 季节需求曲线占比 × 年预测总量 × 趋势系数 × 广告系数 × Listing系数

    年预测总量推算：
    - 若未提供 annual_forecast，用实际销量反推
    - 默认逻辑：7月实际销量20套 → 年预测 = 20 ÷ 1% = 2000
    - 实际销量 > 预测时自动上调后续月份（体现在 trend_coeff 中）

    Args:
        asin: ASIN编码
        target_month: 目标月份
        annual_forecast: 年预测总量（若为None则自动推算）
        actual_sales_7d: 近7天实际销量（用于反推年预测）
        trend_coeff: 趋势系数
        ad_coeff: 广告系数
        listing_coeff: Listing系数
        session: 数据库会话

    Returns:
        预测销量
    """
    # 获取季节曲线占比
    month_ratio = 0.0
    product = None

    if session is not None:
        # 查询产品信息获取节日和子分类
        prod_query = select(Product).where(Product.asin == asin)
        prod_result = await session.execute(prod_query)
        product = prod_result.scalar_one_or_none()

    if product and product.festival:
        # 查询对应的季节曲线
        curve_query = (
            select(SeasonalCurve)
            .where(
                SeasonalCurve.festival == product.festival,
                SeasonalCurve.sub_category == (product.sub_category or "装饰品"),
            )
        )
        curve_result = await session.execute(curve_query)
        curve = curve_result.scalar_one_or_none()

        if curve and curve.month_distribution:
            try:
                distribution = json.loads(curve.month_distribution)
                month_key = str(target_month.month)
                month_ratio = float(distribution.get(month_key, 0))
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"[{asin}] 季节曲线数据解析失败: {curve.month_distribution}")
                month_ratio = 1.0 / 12  # 默认平均分配
    else:
        # 无节日信息或查询失败，默认平均分配
        month_ratio = 1.0 / 12
        logger.info(f"[{asin}] 无季节曲线数据，默认均分 month_ratio={month_ratio:.4f}")

    # 推算年预测总量
    if annual_forecast is None:
        if actual_sales_7d > 0:
            # 用反推逻辑：默认7月占比1%
            annual_forecast = round(actual_sales_7d / 0.01)
        else:
            annual_forecast = 2000  # 默认值
        logger.info(f"[{asin}] 自动推算年预测总量={annual_forecast}")

    # 综合修正系数（新品趋势/广告/Listing全部生效，无权重缩放）
    adjustment = trend_coeff * ad_coeff * listing_coeff

    forecast = round(month_ratio * annual_forecast * adjustment)
    logger.info(
        f"[{asin}] 新品预测: 月占比={month_ratio:.4f}, "
        f"年预测={annual_forecast}, 趋势={trend_coeff:.4f}, "
        f"广告={ad_coeff:.4f}, Listing={listing_coeff:.4f}, "
        f"预测={forecast}"
    )
    return forecast


async def forecast_all_months(
    asin: str,
    forecast_months: int = 6,
    session: Optional[AsyncSession] = None,
    is_new_product: bool = False,
    **kwargs,
) -> dict:
    """
    综合预测未来N月销量

    老品走 old_product_forecast，新品走 new_product_forecast。
    返回按月份拆分的预测明细。

    Args:
        asin: ASIN编码
        forecast_months: 预测月数（默认6个月）
        session: 数据库会话
        is_new_product: 是否为新品
        **kwargs: 传递给具体预测函数的额外参数

    Returns:
        {
            "asin": str,
            "forecast_date": date,
            "is_new_product": bool,
            "monthly": [
                {"month": "2025-08", "forecast": 100},
                ...
            ],
            "total": int
        }
    """
    today = date.today()
    # 从当月开始预测
    current_month = date(today.year, today.month, 1)

    product = None
    if session is not None:
        prod_query = select(Product).where(Product.asin == asin)
        prod_result = await session.execute(prod_query)
        product = prod_result.scalar_one_or_none()

    monthly_forecasts = []

    for i in range(forecast_months):
        # 目标月份
        month = current_month.month + i
        year = current_month.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        target_month = date(year, month, 1)

        if is_new_product:
            forecast = await new_product_forecast(
                asin=asin,
                target_month=target_month,
                session=session,
                **kwargs,
            )
        else:
            forecast = await old_product_forecast(
                asin=asin,
                target_month=target_month,
                session=session,
                **kwargs,
            )

        monthly_forecasts.append({
            "month": target_month.isoformat(),
            "forecast": forecast,
        })

    total = sum(item["forecast"] for item in monthly_forecasts)

    result = {
        "asin": asin,
        "forecast_date": today.isoformat(),
        "is_new_product": is_new_product,
        "monthly": monthly_forecasts,
        "total": total,
    }
    logger.info(
        f"[{asin}] 综合预测完成: 未来{forecast_months}月总计={total}"
    )
    return result
