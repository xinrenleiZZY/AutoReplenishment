"""计算引擎共用小工具（C1 拆分批1 · Phase 3 / B-01）

这些函数原先散落在 app/tasks/calculation_tasks.py 中，被全流程复用；
单独成模块是为了避免 calc.* 与 calculation_tasks 的循环导入。搬运为逐字节剪切，未改逻辑。
"""

import json
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.product import Product
from app.models.sales import SalesData
from app.services import new_product_policy
from app.services.sales_fallback import get_daily_sales_dual


def _safe_int(val, default=0):
    if val is None:
        return default
    return int(val)


def _safe_float(val, default=0.0):
    if val is None:
        return default
    return float(val)


def _to_float_safe(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_step_json(s: str | None):
    """JSON字符串转对象，用于API返回展示"""
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


# ── C1 批2 追加：预测域共用的小工具 ──


async def _recent_daily_orders(asin: str, session: AsyncSession, days: int = 7) -> list:
    """最近N天每日销量/单量（按日期升序）"""
    start = date.today() - timedelta(days=days)
    rows = await session.execute(
        select(SalesData.date, SalesData.sales_qty)
        .where(SalesData.asin == asin, SalesData.date >= start)
        .order_by(SalesData.date)
    )
    vals = [int(r[1] or 0) for r in rows.all()]
    if not vals:
        # 双源优先：SalesData 无近N天记录时，回退 daily_sales_stats 逐日
        fb = await get_daily_sales_dual(asin, start, date.today(), session, use_zero=False)
        vals = [q for _d, q in sorted(fb.items())]
        if not vals:
            vals = [0]
    return vals


async def _get_new_product_cfg(session: AsyncSession) -> dict:
    """读取新品策略配置（DB 可覆盖默认值），并应用到 new_product_policy"""
    from app.services.config_service import get_param

    async def g(key, default):
        v = await get_param(session, key)
        return v if v is not None else default

    cfg = {
        "trigger_days": int(await g("new_product_trigger_days", 3)),
        "trigger_min_order": float(await g("new_product_trigger_min_order", 5.0)),
        "acos_max": float(await g("new_product_acos_max", 0.55)),
        "min_selling_days": int(await g("new_product_min_selling_days", 14)),
        "decoration_buffer_days": int(await g("decoration_buffer_days", 14)),
        "non_decoration_buffer_days": int(await g("non_decoration_buffer_days", 3)),
        "long_term_safety_factor": float(await g("long_term_safety_factor", 1.2)),
        "usd_cny_rate": float(await g("usd_cny_rate", 7.2)),
        # 成本表（三渠道 Profit）模板参数
        "cost_exchange_rate": float(await g("cost_exchange_rate", settings.COST_EXCHANGE_RATE)),
        "cost_referral_ratio": float(await g("cost_referral_ratio", settings.COST_REFERRAL_RATIO)),
        "cost_referral_min": float(await g("cost_referral_min", settings.COST_REFERRAL_MIN)),
        "cost_packing_ratio": float(await g("cost_packing_ratio", settings.COST_PACKING_RATIO)),
        "cost_misc_ratio": float(await g("cost_misc_ratio", settings.COST_MISC_RATIO)),
        "cost_ad_ratio": float(await g("cost_ad_ratio", settings.COST_AD_RATIO)),
        "cost_return_ratio": float(await g("cost_return_ratio", settings.COST_RETURN_RATIO)),
        "cost_inbound_fee": float(await g("cost_inbound_fee", settings.COST_INBOUND_FEE)),
        "cost_storage_fee": float(await g("cost_storage_fee", settings.COST_STORAGE_FEE)),
        "cost_return_threshold": float(await g("cost_return_threshold", settings.COST_RETURN_THRESHOLD)),
        "cost_over_threshold_fee": float(await g("cost_over_threshold_fee", settings.COST_OVER_THRESHOLD_FEE)),
        "cost_default_weight_kg": float(await g("cost_default_weight_kg", settings.COST_DEFAULT_WEIGHT_KG)),
        # 成本表单件附加费用（美元/件，JSON），矫正三渠道Profit时逐项扣减
        "extra_fees_usd": new_product_policy._parse_extra_fees(await g("cost_extra_fees_usd", settings.COST_EXTRA_FEES_USD)),
        "transport_modes": {
            "sea": {
                "label": "海运",
                "slow_days": settings.SEA_SLOW_DAYS, "peak_days": settings.SEA_PEAK_DAYS,
                "slow_fee": float(await g("sea_slow_fee", 15.0)), "peak_fee": float(await g("sea_peak_fee", 17.0)),
            },
            "air": {
                "label": "空派",
                "slow_days": settings.AIR_SLOW_DAYS, "peak_days": settings.AIR_PEAK_DAYS,
                "slow_fee": float(await g("air_slow_fee", 60.0)), "peak_fee": float(await g("air_peak_fee", 70.0)),
            },
            "express": {
                "label": "快递",
                "slow_days": settings.EXPRESS_SLOW_DAYS, "peak_days": settings.EXPRESS_PEAK_DAYS,
                "slow_fee": float(await g("express_slow_fee", 70.0)), "peak_fee": float(await g("express_peak_fee", 80.0)),
            },
        },
    }
    new_product_policy.apply_config(cfg)
    return cfg


def _deep_find_acos(data) -> Optional[float]:
    """深度查找 ACOS 数值（返回结构不固定）"""
    if data is None:
        return None
    if isinstance(data, dict):
        for key, val in data.items():
            if "acos" in str(key).lower() and isinstance(val, (int, float)) and not isinstance(val, bool):
                try:
                    v = float(val)
                    if 0 <= v <= 5:  # 0-500% 范围
                        return v
                except (TypeError, ValueError):
                    pass
        for val in data.values():
            found = _deep_find_acos(val)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _deep_find_acos(item)
            if found is not None:
                return found
    return None


async def _fetch_acos(product: Product) -> Optional[float]:
    """获取30天ACOS：优先产品档案 acos_30d（已由 scripts/sync_acos.py 回填）

    不再逐产品调 SIF：SIF 广告结构接口不返回 ACOS，且外部调用是批量计算的主要耗时点。
    """
    if getattr(product, "acos_30d", None) is not None:
        try:
            return float(product.acos_30d)
        except (ValueError, TypeError):
            pass
    return None


