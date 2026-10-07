"""
销售时间轴管理模块

功能：
1. get_festival_info - 获取节日日期、热卖期等完整信息
2. get_sales_phase - 判断产品当前所处的销售阶段（备货期/增长期/峰值/下降/禁止采购）
3. check_purchase_window - 判断当前是否还能赶上旺季采购
4. get_recommended_transport - 根据时间紧迫度推荐运输方式
"""

import logging
import json
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.festival_calendar import FestivalCalendar
from app.models.product import Product
from app.models.category_leadtime import CategoryLeadtime
from app.config import settings
from app.services.semantic_classify import buffer_days, get_semantic_classification

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
    # 名称可能不匹配（产品"基督教主题" vs 表"Christian基督教主题"），先做名称匹配
    from app.services.festival_lifecycle import match_festival_name

    name = await match_festival_name(festival, session)
    if not name:
        logger.warning(f"未找到节日信息: {festival}")
        return None
    stmt = select(FestivalCalendar).where(FestivalCalendar.festival == name)
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
        "festival_periods": _parse_periods(record.festival_periods),
    }


def _parse_periods(raw: str | None) -> list:
    """解析 festival_periods JSON → [{start: date, end: date, label: str}]"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for p in data or []:
        try:
            start = date.fromisoformat(str(p["start"]))
            end = date.fromisoformat(str(p.get("end") or p["start"]))
            out.append({"start": start, "end": end, "label": str(p.get("label") or "")})
        except (KeyError, ValueError):
            continue
    return out


def _target_festival_date(festival_info: dict, today: date):
    """返回 (目标节日日期, 是否正处于时间段内)

    有多个时间段时取最近的未开始时间段起点；全部已结束时取最后一段结束日；
    无时间段则回退 festival_date。
    """
    periods = festival_info.get("festival_periods") or []
    if not periods:
        return festival_info.get("festival_date"), False
    in_period = any(p["start"] <= today <= p["end"] for p in periods)
    upcoming = [p["start"] for p in periods if p["start"] >= today]
    if upcoming:
        return min(upcoming), in_period
    return max(p["end"] for p in periods), in_period


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

    长期产品（product_type=长期产品）全年销售不过季 → 直接"正常销售"。
    """
    if (getattr(product, "product_type", "") or "") == "长期产品":
        return {
            "phase": PHASE_NORMAL,
            "festival_info": None,
            "days_to_festival": None,
            "is_in_season": True,
            "reason": "长期产品，全年销售不过季",
        }
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
    periods = festival_info.get("festival_periods") or []
    festival_date, in_period = _target_festival_date(festival_info, today)
    listing_start = festival_info.get("listing_start")

    if not festival_date:
        return {
            "phase": PHASE_NORMAL,
            "festival_info": festival_info,
            "days_to_festival": None,
            "is_in_season": True,
            "reason": f"节日 {product.festival} 无具体日期，按正常销售处理",
        }

    # 正处于某销售时间段内 → 峰值
    if in_period:
        return {
            "phase": PHASE_PEAK,
            "festival_info": festival_info,
            "days_to_festival": 0,
            "is_in_season": True,
            "reason": f"正处于 {product.festival} 销售时间段内，销售峰值",
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


def _row_mid(row) -> int:
    """分类工期记录 → 中间值（天）；无效记录返回 None"""
    lo, hi = row.lead_time_min, row.lead_time_max
    if lo and hi:
        return (lo + hi) // 2
    if lo:
        return lo
    if hi:
        return hi
    return None


async def resolve_lead_time(product: Product, session: AsyncSession) -> int:
    """解析大货工期：优先产品实际填写，否则按分类工期表(category_leadtimes)匹配，兜底30天

    匹配规则：
      0. products.lead_time 优先；
      1. products.sub_category 与分类工期表二级分类(level2_category)精确匹配（优先"一级+二级"联合）→ 取该行工期中间值；
      2. products.category 精确匹配二级分类(level2_category) → 取该行工期中间值；
      3. products.category 精确匹配一级分类(level1_category)：
         - 一级分类下有二级明细（分类挂在大类上）→ 按各二级最长工期（max 天数最大）取；
         - 一级分类为独立行（level2 为空）→ 用该行工期；
      4. 兜底 30 天。
    """
    if product.lead_time:
        return int(product.lead_time)

    # ── 0) 优先用二级分类(sub_category)精确匹配工期 ──
    #       - 优先"一级+二级"联合匹配，避免同名二级跨大类误配；
    #       - 匹配不到时再仅按二级分类匹配。
    sub_category = (product.sub_category or "").strip()
    if sub_category:
        _base = select(CategoryLeadtime).where(CategoryLeadtime.level2_category == sub_category)
        _cat = (product.category or "").strip()
        _row = None
        if _cat:
            _row = (await session.execute(_base.where(CategoryLeadtime.level1_category == _cat))).scalar_one_or_none()
        if _row is None:
            _row = (await session.execute(_base)).scalar_one_or_none()
        if _row is not None:
            _mid = _row_mid(_row)
            return _mid if _mid is not None else 30

    category = (product.category or "").strip()
    if not category:
        return 30

    # 1) 二级分类精确匹配（如 磁贴/硅胶类/纸质挂饰类）
    row = (await session.execute(
        select(CategoryLeadtime).where(CategoryLeadtime.level2_category == category)
    )).scalar_one_or_none()
    if row is not None:
        mid = _row_mid(row)
        return mid if mid is not None else 30

    # 2) 一级分类匹配
    rows = (await session.execute(
        select(CategoryLeadtime).where(CategoryLeadtime.level1_category == category)
    )).scalars().all()
    if not rows:
        return 30
    if any(r.level2_category for r in rows):
        # 分类挂在一级大类上 → 按最长工期（各二级 max 天数的最大值）
        ends = [r.lead_time_max or r.lead_time_min for r in rows
                if (r.lead_time_max or r.lead_time_min) is not None]
        return max(ends) if ends else 30
    # 一级独立行
    mid = _row_mid(rows[0])
    return mid if mid is not None else 30


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

    长期产品（product_type=长期产品）全年销售不过季 → 无时间限制可采购。
    """
    if (getattr(product, "product_type", "") or "") == "长期产品":
        return {
            "can_purchase": True,
            "recommended_transport": "海运",
            "latest_purchase_date": None,
            "days_remaining": 365,
            "reason": "长期产品，全年销售不过季，无时间限制",
        }
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
    periods = festival_info.get("festival_periods") or []
    festival_date, in_period = _target_festival_date(festival_info, today)
    if not festival_date:
        return {
            "can_purchase": True,
            "recommended_transport": "海运",
            "latest_purchase_date": None,
            "days_remaining": 365,
            "reason": f"节日 {product.festival} 信息不全，无时间限制",
        }
    # 已进入或已过全部销售时间段 → 无法赶上
    if in_period or (periods and not any(p["start"] >= today for p in periods)):
        return {
            "can_purchase": False,
            "recommended_transport": "无法赶上",
            "latest_purchase_date": None,
            "days_remaining": 0,
            "reason": f"{product.festival} 销售时间段已开始或已结束，无法赶上采购窗口",
        }
    lead_time = await resolve_lead_time(product, session)

    # 判断产品类型以确定售卖截止日（语义分类：装饰品/非装饰品，取自 semantic_classifications 表）
    semantic = await get_semantic_classification(session, product.asin)
    is_decoration = (semantic or "").strip() == "装饰品"

    # 运输时间
    sea_days = settings.SEA_PEAK_DAYS if is_decoration else settings.SEA_SLOW_DAYS
    air_days = settings.AIR_PEAK_DAYS
    express_days = settings.EXPRESS_PEAK_DAYS

    # 缓存天数判定：装饰品=14，非装饰品=3
    selling_end_days_before = buffer_days(semantic)
    selling_end_date = festival_date - timedelta(days=selling_end_days_before)

    # 计算各运输方式的最晚采购日期（结果统一再提前 PURCHASE_BUFFER_DAYS 天作为缓冲）
    PURCHASE_BUFFER_DAYS = 3
    latest_sea = selling_end_date - timedelta(days=lead_time + sea_days + PURCHASE_BUFFER_DAYS)
    latest_air = selling_end_date - timedelta(days=lead_time + air_days + PURCHASE_BUFFER_DAYS)
    latest_express = selling_end_date - timedelta(days=lead_time + express_days + PURCHASE_BUFFER_DAYS)

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
        # 仅快递可赶上 = 备货窗口已近关闭，采购过急且快递运费过高 → 视为过季/无法从容备货，不采购
        return {
            "can_purchase": False,
            "recommended_transport": "仅快递",
            "latest_purchase_date": latest_express,
            "days_remaining": days_to_express,
            "reason": (f"仅快递可赶上（最晚{latest_express}，剩余{days_to_express}天），"
                       "海运/空运均来不及，备货过急运费过高，视为过季不采购"),
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
