"""
未来销量预测模块

提供老品和新品两种预测模型，以及综合预测未来N月功能。
"""

import calendar
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sales import SalesData
from app.models.product import Product
from app.services.sales_fallback import sum_daily_sales_dual
from app.services.festival_lifecycle import lifecycle_end_date
from app.config import settings

logger = logging.getLogger(__name__)


def month_lifecycle_ratio(year: int, month: int, lifecycle_end: date | None) -> float:
    """结束月按剩余天数折算的比例（把节日生命周期截断从「月」细化到「日」）

    - 1.0：lifecycle_end 为空，或落在该月最后一天之后（整月计入）
    - 0.0：该月在 lifecycle_end 之后（整月不计入）
    - 0<x<1：lifecycle_end 落在该月内（如 10-20 结束 → 只计 10-01~10-20）
    """
    if lifecycle_end is None:
        return 1.0
    last_day = calendar.monthrange(year, month)[1]
    month_start = date(year, month, 1)
    if lifecycle_end >= date(year, month, last_day):
        return 1.0
    if lifecycle_end < month_start:
        return 0.0
    return ((lifecycle_end - month_start).days + 1) / last_day


def compute_trend_coeff(seven_volume: int | None, fourteen_volume: int | None, thirty_volume: int | None) -> float:
    """趋势系数：近7天日均 vs 前7天日均增长率（来自每日快照 7/14/30天销量）"""
    seven = int(seven_volume or 0)
    fourteen = int(fourteen_volume or 0)
    recent7 = seven / 7
    prev7 = max(fourteen - seven, 0) / 7
    if prev7 > 0:
        growth = (recent7 - prev7) / prev7
    else:
        growth = 0.0
    return round(min(max(1.0 + growth, 0.5), 1.5), 3)


def compute_ad_coeff(ad_spend_ratio: float | None = None, acos: float | None = None) -> float:
    """广告系数：30天广告花费占销售额比（近似ACOS）。ACOS越低广告效率越高→上调预测"""
    ratio = acos if acos is not None else ad_spend_ratio
    if ratio is None:
        return 1.0
    try:
        ratio = float(ratio)
    except (ValueError, TypeError):
        return 1.0
    if ratio <= 0.15:
        return 1.10
    if ratio <= 0.30:
        return 1.05
    if ratio <= 0.55:
        return 1.00
    return 0.85


def compute_listing_coeff(rating: float | None = None, review_growth: float | None = None) -> float:
    """Listing健康度系数：评分（4星为基准）+ 评论增长"""
    base = 1.0
    if rating is not None:
        try:
            base += (float(rating) - 4.0) * 0.05
        except (ValueError, TypeError):
            pass
    if review_growth is not None:
        try:
            base += max(min(float(review_growth), 0.1), -0.1)
        except (ValueError, TypeError):
            pass
    return round(min(max(base, 0.8), 1.2), 3)


def compute_market_coeff(category_rank: int | None = None, prev_rank: int | None = None) -> float:
    """市场竞争系数：类目排名变化（上升→上调）；无历史排名时按当前档位"""
    if category_rank is None:
        return 1.0
    try:
        rank = float(category_rank)
    except (ValueError, TypeError):
        return 1.0
    if prev_rank is not None:
        try:
            prev = float(prev_rank)
            if prev > 0:
                change = (prev - rank) / prev  # 正值 = 排名上升
                return round(min(max(1.0 + change * 0.3, 0.8), 1.2), 3)
        except (ValueError, TypeError):
            pass
    if rank < 10000:
        return 1.1
    if rank <= 50000:
        return 1.0
    return 0.9


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
    老品预测模型（需求文档第七章 v2）

    未来销量 = 历史同期销量 × 综合修正系数

    权重分配（文档定义）：
      历史销售基准 40%  ← 去年同期销量（订单量）
      当前销售趋势 25%  ← 7/14/30天销量增长率
      市场竞争表现 15%  ← 核心关键词排名变化 + 类目排名变化
      广告驱动因素 10%  ← ACOS + 广告订单占比
      Listing健康度 10% ← 评分 + Review数量增长

    综合修正系数 = 0.40 + (趋势系数-1)×0.25 + (市场系数-1)×0.15
                         + (广告系数-1)×0.10 + (Listing系数-1)×0.10

    Args:
        asin: ASIN编码
        target_month: 目标月份（取该月第一天）
        trend_coeff: 趋势系数（基于7/14/30天销量增长率）
        market_coeff: 市场系数（基于关键词排名+类目排名变化，SIF获取）
        ad_coeff: 广告系数（基于ACOS+广告订单占比，SIF获取）
        listing_coeff: Listing系数（基于评分+Review增长，SIF获取）
        session: 数据库会话

    Returns:
        预测销量（四舍五入取整）
    """
    # 权重（需求文档第七章）
    HISTORY_WEIGHT = 0.40
    TREND_WEIGHT = 0.25
    MARKET_WEIGHT = 0.15
    AD_WEIGHT = 0.10
    LISTING_WEIGHT = 0.10

    # 查询历史同期销量（去年同月，对应"历史销售基准"）
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

    # 双源优先：daily_sales_stats（逐日实抓）读去年同月销量，缺时回退 sales_data
    if historical_sales == 0 and session is not None:
        fb_hist = await sum_daily_sales_dual(asin, last_year_start, last_year_end, session)
        if fb_hist:
            historical_sales = fb_hist

    if historical_sales == 0:
        logger.warning(f"[{asin}] 去年同月({last_year_start})无历史销量数据")
        return 0

    # 计算综合修正系数（需求文档权重）
    adjustment = (
        HISTORY_WEIGHT                       # 历史基准权重40%（基数）
        + (trend_coeff - 1.0) * TREND_WEIGHT      # 趋势修正 25%
        + (market_coeff - 1.0) * MARKET_WEIGHT    # 市场修正 15%
        + (ad_coeff - 1.0) * AD_WEIGHT            # 广告修正 10%
        + (listing_coeff - 1.0) * LISTING_WEIGHT  # Listing修正 10%
    )

    forecast = round(historical_sales * adjustment)
    logger.info(
        f"[{asin}] 老品预测: 历史销量={historical_sales}, "
        f"趋势={trend_coeff:.4f}(25%), 市场={market_coeff:.4f}(15%), "
        f"广告={ad_coeff:.4f}(10%), Listing={listing_coeff:.4f}(10%), "
        f"历史权重=40%, 修正系数={adjustment:.4f}, 预测={forecast}"
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
    # 季节曲线已停用：按月均分（1/12）
    month_ratio = 1.0 / 12

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
    若节日生命周期结束时间落在预测窗口内（除外节日见 festival_lifecycle.lifecycle_end_date），
    则只预测到该结束日，结束月按剩余天数折算，其后月份不再计入。

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

    # 节日生命周期结束时间落在预测窗口内 → 只预测到结束月即可
    # （除外节日：长期产品、感恩节、圣诞节、秋季类/冬季类、农历新年、跨年、情人节等）
    lifecycle_end = None
    if product is not None:
        lifecycle_end = await lifecycle_end_date(product, session, today)

    monthly_forecasts = []

    for i in range(forecast_months):
        # 目标月份
        month = current_month.month + i
        year = current_month.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        target_month = date(year, month, 1)

        # 节日生命周期结束日截断（日粒度）：结束月按剩余天数折算，其后月份不再计入
        ratio = month_lifecycle_ratio(year, month, lifecycle_end)
        if ratio <= 0:
            break

        if is_new_product:
            new_kwargs = {k: v for k, v in kwargs.items()
                          if k in ("trend_coeff", "ad_coeff", "listing_coeff")}
            forecast = await new_product_forecast(
                asin=asin,
                target_month=target_month,
                session=session,
                **new_kwargs,
            )
        else:
            old_kwargs = {k: v for k, v in kwargs.items()
                          if k in ("trend_coeff", "market_coeff", "ad_coeff", "listing_coeff")}
            forecast = await old_product_forecast(
                asin=asin,
                target_month=target_month,
                session=session,
                **old_kwargs,
            )

        # 结束月按剩余天数折算（如生命周期 10-20 结束 → 10 月只计 10-01~10-20）
        if ratio < 1:
            forecast = round(forecast * ratio)

        monthly_forecasts.append({
            "month": target_month.isoformat(),
            "forecast": forecast,
            "days_ratio": round(ratio, 4),
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
        f"[{asin}] 综合预测完成: 计划{forecast_months}月，实预测{len(monthly_forecasts)}月"
        f"{f'（截止节日生命周期结束 {lifecycle_end.isoformat()}）' if lifecycle_end else ''}，总计={total}"
    )
    return result
