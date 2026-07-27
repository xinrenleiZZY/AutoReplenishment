"""
销售时间轴管理模块

功能：
1. get_festival_info - 获取节日日期、热卖期等完整信息
2. get_sales_phase - 判断产品当前所处的销售阶段（备货期/增长期/峰值/下降/禁止采购）
3. check_purchase_window - 判断当前是否还能赶上旺季采购
4. get_recommended_transport - 根据时间紧迫度推荐运输方式
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.festival_calendar import FestivalCalendar
from app.models.product import Product
from app.config import settings

logger = logging.getLogger(__name__)

# 销售阶段
PHASE_PREP = "备货期"
PHASE_GROWTH = "增长期"
PHASE_PEAK = "峰值"
PHASE_DECLINE = "下降"
PHASE_BANNED = "禁止采购"
PHASE_NORMAL = "正常销售"  # 非节日产品
PHASE_UNKNOWN = "未知"


async def get_festival_info(festival: str, session: AsyncSession) -> Optional[dict]:
    """获取节日完整信息

    Args:
        festival: 节日名称
        session: 数据库会话

    Returns:
        dict: {
            "festival": str,
            "listing_start": date,       # 上架开卖时间
            "festival_date": date,        # 节日日期
            "festival_end": date,         # 结束时间
            "hot_start_month": int,       # 热卖开始月份
            "hot_end_month": int,         # 热卖结束月份
            "hot_period": str,            # 热卖期描述
        } 或 None
    """
    stmt = select(FestivalCalendar).where(FestivalCalendar.festival == festival)
    result = await session.execute(stmt)
    records = result.scalars().all()

    if not records:
        logger.warning(f"未找到节日信息: {festival}")
        return None

    # 返回第一个匹配的记录（如果有多条，取festival_date最近的）
    record = records[0]
    for r in records:
        if r.festival_date and record.festival_date:
            if abs((r.festival_date - datetime.now()).days) < abs((record.festival_date - datetime.now()).days):
                record = r

    return {
        "festival": record.festival,
        "listing_start": record.listing_start.date() if record.listing_start else None,
        "festival_date": record.festival_date.date() if record.festival_date else None,
        "festival_end": record.festival_end.date() if record.festival_end else None,
        "hot_start_month": record.hot_start_month,
        "hot_end_month": record.hot_end_month,
        "hot_period": record.hot_period,
    }


async def get_sales_phase(product: Product, session: AsyncSession) -> dict:
    """判断产品当前所处的销售阶段

    针对节日产品判断所处的销售周期阶段，非节日产品返回"正常销售"。

    Args:
        product: 产品对象
        session: 数据库会话

    Returns:
        dict: {
            "phase": str,               # 销售阶段
            "festival_info": dict,       # 节日信息
            "days_to_festival": int,     # 距离节日天数
            "is_in_season": bool,        # 是否在销售季节内
            "reason": str,               # 判断理由
        }
    """
    if not product.festival:
        return {
            "phase": PHASE_NORMAL,
            "festival_info": None,
            "days_to_festival": None,
            "is_in_season": True,
            "reason": "非节日产品，正常销售",
        }

    festival_info = await get_festival_info(product.festival, session)
    if not festival_info:
        return {
            "phase": PHASE_UNKNOWN,
            "festival_info": None,
            "days_to_festival": None,
            "is_in_season": True,
            "reason": f"未找到节日 {product.festival} 的信息",
        }

    today = date.today()
    festival_date = festival_info.get("festival_date")
    listing_start = festival_info.get("listing_start")

    if not festival_date:
        return {
            "phase": PHASE_NORMAL,
            "festival_info": festival_info,
            "days_to_festival": None,
            "is_in_season": True,
            "reason": f"节日 {product.festival} 无具体日期，按正常销售处理",
        }

    days_to_festival = (festival_date - today).days
    current_month = today.month

    # 判断是否在热卖月份内
    hot_start = festival_info.get("hot_start_month")
    hot_end = festival_info.get("hot_end_month")
    is_in_hot_period = False
    if hot_start and hot_end:
        if hot_start <= hot_end:
            is_in_hot_period = hot_start <= current_month <= hot_end
        else:
            # 跨年热卖期，如 10-1月
            is_in_hot_period = current_month >= hot_start or current_month <= hot_end

    if listing_start:
        days_since_listing = (today - listing_start).days
    else:
        days_since_listing = None

    # 判断销售阶段
    if days_to_festival < 0:
        # 节后
        if days_to_festival > -30:
            phase = PHASE_DECLINE
            reason = f"节日已过{days_to_festival}天，尾期销售中"
        else:
            phase = PHASE_BANNED
            reason = "节日已结束超过30天，禁止采购"
    elif days_to_festival <= 7:
        phase = PHASE_PEAK
        reason = f"距离节日仅{days_to_festival}天，销售峰值期"
    elif days_to_festival <= 30:
        if is_in_hot_period:
            phase = PHASE_PEAK
            reason = f"距离节日{days_to_festival}天且在热卖期内，销售峰值期"
        else:
            phase = PHASE_GROWTH
            reason = f"距离节日{days_to_festival}天，仍在增长期"
    elif days_to_festival <= 60:
        phase = PHASE_GROWTH if is_in_hot_period else PHASE_PREP
        reason = f"距离节日{days_to_festival}天，备货/增长期"
    elif days_to_festival <= 120:
        phase = PHASE_PREP
        reason = f"距离节日{days_to_festival}天，备货期"
    else:
        phase = PHASE_BANNED
        reason = f"距离节日还有{days_to_festival}天，远未到采购窗口"

    is_in_season = phase in (PHASE_PREP, PHASE_GROWTH, PHASE_PEAK, PHASE_DECLINE)

    return {
        "phase": phase,
        "festival_info": festival_info,
        "days_to_festival": days_to_festival,
        "is_in_season": is_in_season,
        "reason": reason,
    }


async def check_purchase_window(product: Product, session: AsyncSession) -> dict:
    """判断当前是否还能赶上旺季采购

    考虑因素：
    - 产品工期（lead_time）
    - 运输时间（海运/空派/快递）
    - 距离节日的天数
    - 装饰品vs非装饰品（售卖截止日不同）

    Args:
        product: 产品对象
        session: 数据库会话

    Returns:
        dict: {
            "can_purchase": bool,              # 是否能采购
            "recommended_transport": str,      # 推荐运输方式
            "latest_purchase_date": date,      # 最晚采购日期
            "days_remaining": int,             # 剩余天数
            "reason": str,
        }
    """
    if not product.festival:
        return {
            "can_purchase": True,
            "recommended_transport": "海运",
            "latest_purchase_date": None,
            "days_remaining": 365,
            "reason": "非节日产品，无时间限制",
        }

    festival_info = await get_festival_info(product.festival, session)
    if not festival_info or not festival_info.get("festival_date"):
        return {
            "can_purchase": True,
            "recommended_transport": "海运",
            "latest_purchase_date": None,
            "days_remaining": 365,
            "reason": f"节日 {product.festival} 信息不全，无时间限制",
        }

    today = date.today()
    festival_date = festival_info["festival_date"]
    lead_time = product.lead_time or 30

    # 判断产品类型以确定售卖截止日
    is_decoration = (product.sub_category or "").strip() in ("装饰品",)

    # 运输时间
    sea_days = settings.SEA_PEAK_DAYS if is_decoration else settings.SEA_SLOW_DAYS
    air_days = settings.AIR_PEAK_DAYS
    express_days = settings.EXPRESS_PEAK_DAYS

    # 装饰品：节前12天，非装饰品：节前3天
    if is_decoration:
        selling_end_days_before = 12
    else:
        selling_end_days_before = 3
    selling_end_date = festival_date - timedelta(days=selling_end_days_before)

    # 计算各运输方式的最晚采购日期
    latest_sea = selling_end_date - timedelta(days=lead_time + sea_days)
    latest_air = selling_end_date - timedelta(days=lead_time + air_days)
    latest_express = selling_end_date - timedelta(days=lead_time + express_days)

    days_to_sea = (latest_sea - today).days
    days_to_air = (latest_air - today).days
    days_to_express = (latest_express - today).days

    if days_to_sea >= 0:
        return {
            "can_purchase": True,
            "recommended_transport": "海运",
            "latest_purchase_date": latest_sea,
            "days_remaining": days_to_sea,
            "reason": f"海运可按时到仓，最晚采购日{latest_sea}，剩余{days_to_sea}天",
        }
    elif days_to_air >= 0:
        return {
            "can_purchase": True,
            "recommended_transport": "空派",
            "latest_purchase_date": latest_air,
            "days_remaining": days_to_air,
            "reason": f"海运来不及，空派可赶上，最晚采购日{latest_air}，剩余{days_to_air}天",
        }
    elif days_to_express >= 0:
        return {
            "can_purchase": True,
            "recommended_transport": "快递",
            "latest_purchase_date": latest_express,
            "days_remaining": days_to_express,
            "reason": f"仅快递可赶上，最晚采购日{latest_express}，剩余{days_to_express}天",
        }
    else:
        return {
            "can_purchase": False,
            "recommended_transport": "无法赶上",
            "latest_purchase_date": latest_express,
            "days_remaining": days_to_express,
            "reason": f"任何运输方式均无法赶上销售窗口，最晚已过{abs(days_to_express)}天",
        }


async def get_recommended_transport(
    product: Product,
    session: AsyncSession,
    preferred_mode: str = "sea",
) -> dict:
    """根据时间紧迫度推荐运输方式

    Args:
        product: 产品对象
        session: 数据库会话
        preferred_mode: 首选运输方式（sea/air/express）

    Returns:
        dict: {"mode": str, "days": int, "reason": str}
    """
    window = await check_purchase_window(product, session)

    transport_modes = {"海运": settings.SEA_PEAK_DAYS, "空派": settings.AIR_PEAK_DAYS, "快递": settings.EXPRESS_PEAK_DAYS}
    mode_cn = {"sea": "海运", "air": "空派", "express": "快递"}

    preferred_cn = mode_cn.get(preferred_mode, "海运")
    preferred_days = transport_modes.get(preferred_cn, 30)

    if window["can_purchase"]:
        return {
            "mode": window["recommended_transport"],
            "days": transport_modes.get(window["recommended_transport"], 30),
            "reason": window["reason"],
        }
    else:
        return {
            "mode": "无法采购",
            "days": 999,
            "reason": window["reason"],
        }
