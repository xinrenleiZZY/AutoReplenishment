"""
库存健康分析模块

提供可用库存计算、库存覆盖天数、补货周期、紧急程度评分等功能。
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventorySnapshot
from app.models.product import Product
from app.models.config import ConfigParam
from app.config import settings

logger = logging.getLogger(__name__)


def calculate_available_stock(inventory: InventorySnapshot) -> int:
    """
    计算可用库存

    可用库存 = FBA可售 + FBA预留 + FBA在途 + 本地库存 + 采购单待到货

    Args:
        inventory: 库存快照对象

    Returns:
        可用库存数量
    """
    if inventory is None:
        return 0

    available = (
        (inventory.fba_available or 0)
        + (inventory.fba_reserved or 0)
        + (inventory.fba_inbound or 0)
        + (inventory.local_stock or 0)
        + (inventory.purchase_on_order or 0)
    )
    return available


def calculate_inventory_days(available_stock: int, avg_daily_sales: float) -> int:
    """
    计算库存覆盖天数

    Args:
        available_stock: 可用库存
        avg_daily_sales: 日均销量

    Returns:
        库存覆盖天数（向下取整），若日均销量<=0返回999
    """
    if avg_daily_sales <= 0:
        return 999
    return int(available_stock / avg_daily_sales)


async def get_transport_days(
    mode: str,
    is_peak_season: bool,
    session: Optional[AsyncSession] = None,
) -> int:
    """
    获取运输时间

    优先从数据库配置表查询，若失败则回退到settings中的默认值。

    Args:
        mode: 运输方式（sea/air/express）
        is_peak_season: 是否旺季

    Returns:
        运输天数
    """
    # 默认值映射
    default_map = {
        "sea": settings.SEA_SLOW_DAYS if not is_peak_season else settings.SEA_PEAK_DAYS,
        "air": settings.AIR_SLOW_DAYS if not is_peak_season else settings.AIR_PEAK_DAYS,
        "express": settings.EXPRESS_SLOW_DAYS if not is_peak_season else settings.EXPRESS_PEAK_DAYS,
    }

    if session is not None:
        try:
            config_key = f"{mode}_{'peak' if is_peak_season else 'slow'}_days"
            query = select(ConfigParam).where(ConfigParam.param_key == config_key)
            result = await session.execute(query)
            config_row = result.scalar_one_or_none()
            if config_row and config_row.param_value:
                return int(config_row.param_value)
        except Exception as e:
            logger.warning(f"查询运输配置失败(key={config_key}): {e}")

    return default_map.get(mode, 30)


async def get_replenishment_cycle(
    product: Product,
    transport_mode: str = "sea",
    is_peak_season: bool = False,
    session: Optional[AsyncSession] = None,
) -> int:
    """
    计算补货周期

    补货周期 = 工期 + 运输时间 + 安全库存时间

    Args:
        product: 产品对象
        transport_mode: 运输方式（sea/air/express）
        is_peak_season: 是否旺季
        session: 数据库会话

    Returns:
        补货周期（天）
    """
    lead_time = product.lead_time or 0
    transport_days = await get_transport_days(transport_mode, is_peak_season, session)
    safety_stock_days = settings.SAFE_STOCK_DAYS

    cycle = lead_time + transport_days + safety_stock_days
    logger.info(
        f"[{product.asin}] 补货周期={cycle}天 (工期={lead_time}, "
        f"运输={transport_days}, 安全库存={safety_stock_days})"
    )
    return cycle


def calculate_urgency_score(inventory_days: int) -> int:
    """
    计算紧急程度评分

    规则:
      - 30-90天  → 40分
      - 15-30天  → 80分
      - <15天    → 100分
      - 91-180天 → 0分
      - >180天   → 0分

    Args:
        inventory_days: 库存覆盖天数

    Returns:
        紧急程度评分（0-100）
    """
    if inventory_days < 0:
        return 100
    if 0 <= inventory_days < 15:
        return 100
    if 15 <= inventory_days <= 30:
        return 80
    if 30 < inventory_days <= 90:
        return 40
    # 91天及以上（含999）
    return 0
