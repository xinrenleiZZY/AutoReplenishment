"""产品生命周期识别模块

功能：
1. identify_stage - 判断新品/老品（上架≤1年=新品）
2. identify_lifecycle - 识别产品生命周期阶段（启动期/增长期/热卖期/成熟期/下降期/清库存期）
3. calculate_product_level - 计算产品等级（S/A/B/C/D）
"""

import logging
from datetime import date, timedelta
from typing import List, Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.sales import SalesData

logger = logging.getLogger(__name__)

# 生命周期常量
LIFECYCLE_LAUNCH = "启动期"       # 新品默认
LIFECYCLE_GROWTH = "增长期"       # 快速增长
LIFECYCLE_HOT = "热卖期"          # 热卖中
LIFECYCLE_MATURE = "成熟期"       # 稳定成熟
LIFECYCLE_DECLINE = "下降期"      # 销量下降
LIFECYCLE_CLEARANCE = "清库存期"  # 清库存

# 产品等级阈值（年销量）
LEVEL_S_THRESHOLD = 5000
LEVEL_A_THRESHOLD = 2000
LEVEL_B_THRESHOLD = 1000
LEVEL_C_THRESHOLD = 300
LEVEL_D_THRESHOLD = 1

# 新品判定天数
NEW_PRODUCT_DAYS = 365


async def identify_stage(asin: str, session: AsyncSession) -> str:
    """判断产品阶段：新品/老品

    上架日期 ≤ 当前日期1年 = 新品，否则 = 老品。
    如果产品没有上架日期，默认返回"老品"。

    Args:
        asin: ASIN编码
        session: 数据库会话

    Returns:
        "新品" 或 "老品"
    """
    result = await session.execute(
        select(Product.list_date).where(Product.asin == asin)
    )
    list_date = result.scalar_one_or_none()

    if not list_date:
        logger.warning(f"产品 {asin} 无上架日期，默认老品")
        return "老品"

    days_since_listing = (date.today() - list_date).days
    return "新品" if days_since_listing <= NEW_PRODUCT_DAYS else "老品"


async def identify_lifecycle(product: Product, session: AsyncSession) -> dict:
    """识别产品生命周期阶段

    新品（上架≤1年）→ 默认"启动期"
    老品依据：上架时间、近30天/60天销量增长率判断。

    Args:
        product: Product ORM对象
        session: 数据库会话

    Returns:
        dict: {
            "asin": str,
            "lifecycle": str,       # 生命周期阶段
            "stage": str,           # 新品/老品
            "growth_rate": float,   # 近30天销量增长率
            "sales_30": int,        # 近30天销量
            "sales_60": int,        # 30-60天前销量
            "reason": str,          # 判断理由
        }
    """
    today = date.today()

    # 判断新品老品
    stage = "新品"
    if product.list_date:
        days_since_listing = (today - product.list_date).days
        if days_since_listing > NEW_PRODUCT_DAYS:
            stage = "老品"

    if stage == "新品":
        return {
            "asin": product.asin,
            "lifecycle": LIFECYCLE_LAUNCH,
            "stage": "新品",
            "growth_rate": 0,
            "sales_30": 0,
            "sales_60": 0,
            "reason": "上架≤1年，默认启动期",
        }

    # 老品：分析近30天 vs 前30天销量增长率
    thirty_days_ago = today - timedelta(days=30)
    sixty_days_ago = today - timedelta(days=60)

    # 近30天销量
    result_30 = await session.execute(
        select(func.coalesce(func.sum(SalesData.sales_qty), 0)).where(
            SalesData.asin == product.asin,
            SalesData.date >= thirty_days_ago,
            SalesData.date < today,
        )
    )
    sales_30 = result_30.scalar() or 0

    # 30-60天前销量
    result_60 = await session.execute(
        select(func.coalesce(func.sum(SalesData.sales_qty), 0)).where(
            SalesData.asin == product.asin,
            SalesData.date >= sixty_days_ago,
            SalesData.date < thirty_days_ago,
        )
    )
    sales_60 = result_60.scalar() or 0

    # 计算增长率
    growth_rate = 0.0
    if sales_60 > 0:
        growth_rate = round((sales_30 - sales_60) / sales_60, 4)

    # 根据增长率判断生命周期
    if growth_rate >= 0.5:
        lifecycle = LIFECYCLE_GROWTH
        reason = f"近30天销量增长率≥50%（{growth_rate:.1%}），快速增长中"
        if sales_30 > 100:
            lifecycle = LIFECYCLE_HOT
            reason = f"近30天销量高且增长率≥50%（{growth_rate:.1%}），热卖中"
    elif growth_rate >= 0.2:
        lifecycle = LIFECYCLE_HOT
        reason = f"近30天销量增长率≥20%（{growth_rate:.1%}），热卖中"
    elif growth_rate >= -0.2:
        lifecycle = LIFECYCLE_MATURE
        reason = f"近30天销量增长率在±20%之间（{growth_rate:.1%}），表现稳定"
    elif growth_rate >= -0.5:
        lifecycle = LIFECYCLE_DECLINE
        reason = f"近30天销量下降20%-50%（{growth_rate:.1%}），需关注"
    else:
        lifecycle = LIFECYCLE_CLEARANCE
        reason = f"近30天销量下降超50%（{growth_rate:.1%}），建议清库存"

    return {
        "asin": product.asin,
        "lifecycle": lifecycle,
        "stage": "老品",
        "growth_rate": growth_rate,
        "sales_30": sales_30,
        "sales_60": sales_60,
        "reason": reason,
    }


async def calculate_product_level(
    product: Product,
    sales_data: Optional[List[SalesData]] = None,
    session: Optional[AsyncSession] = None,
) -> dict:
    """计算产品等级

    老品按年销量：S(≥5000), A(2000-4999), B(1000-1999), C(300-999), D(1-299)
    新品按预测销量（暂无预测时默认返回C级）。

    Args:
        product: Product ORM对象
        sales_data: 当年销量数据列表（可选，老品计算用）
        session: 数据库会话（当sales_data为None时用于查询）

    Returns:
        dict: {
            "asin": str,
            "product_level": str,  # S/A/B/C/D
            "total_sales": int,    # 年销量
            "stage": str,          # 新品/老品
        }
    """
    today = date.today()

    # 判断新品老品
    stage = "新品"
    if product.list_date:
        days_since_listing = (today - product.list_date).days
        if days_since_listing > NEW_PRODUCT_DAYS:
            stage = "老品"

    if stage == "新品":
        return {
            "asin": product.asin,
            "product_level": "C",
            "total_sales": 0,
            "stage": "新品",
        }

    # 老品：计算年销量
    total_sales = 0
    if sales_data:
        total_sales = sum(s.sales_qty for s in sales_data)
    elif session:
        result = await session.execute(
            select(func.coalesce(func.sum(SalesData.sales_qty), 0)).where(
                SalesData.asin == product.asin,
                func.extract("year", SalesData.date) == today.year,
            )
        )
        total_sales = result.scalar() or 0

    level = _map_sales_to_level(total_sales)
    return {
        "asin": product.asin,
        "product_level": level,
        "total_sales": total_sales,
        "stage": "老品",
    }


def _map_sales_to_level(total_sales: int) -> str:
    """根据年销量映射产品等级"""
    if total_sales >= LEVEL_S_THRESHOLD:
        return "S"
    elif total_sales >= LEVEL_A_THRESHOLD:
        return "A"
    elif total_sales >= LEVEL_B_THRESHOLD:
        return "B"
    elif total_sales >= LEVEL_C_THRESHOLD:
        return "C"
    elif total_sales >= LEVEL_D_THRESHOLD:
        return "D"
    return "D"
