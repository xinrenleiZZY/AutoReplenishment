"""
采购决策评分模型

实现6因素加权评分，输出综合评分和采购建议等级。
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── 各因素评分规则 ──────────────────────────────────────────────


def _score_stockout_risk(inventory_days: int, replenishment_cycle: int) -> int:
    """
    断货风险评分（权重25%）

    规则:
      库存覆盖/补货周期 ≤ 20%   → 100分（高风险，亟需补货）
      20% < 比值 ≤ 50%          → 80分
      50% < 比值 ≤ 80%          → 60分
      80% < 比值 ≤ 100%         → 30分
      比值 > 100%（覆盖>周期）   → 0分（无断货风险）

    注意：比值越低越危险，评分越高（急需补货）
    """
    if replenishment_cycle <= 0:
        return 100

    ratio = inventory_days / replenishment_cycle

    if ratio <= 0.20:
        return 100
    elif ratio <= 0.50:
        return 80
    elif ratio <= 0.80:
        return 60
    elif ratio <= 1.0:
        return 30
    else:
        return 0


def _score_sales_trend(
    sales_growth_rate: Optional[float],
) -> int:
    """
    销量趋势评分（权重25%）

    规则:
      增长率 ≥ 30%   → 100分
      10% ≤ 增长率 < 30% → 80分
      -10% < 增长率 < 10% → 60分（稳定）
      -30% ≤ 增长率 ≤ -10% → 40分
      增长率 < -30%  → 20分

    Args:
        sales_growth_rate: 销售增长率（小数，如0.3表示30%）
    """
    if sales_growth_rate is None:
        return 60  # 无数据时默认稳定

    if sales_growth_rate >= 0.30:
        return 100
    elif sales_growth_rate >= 0.10:
        return 80
    elif sales_growth_rate > -0.10:
        return 60
    elif sales_growth_rate >= -0.30:
        return 40
    else:
        return 20


def _score_profit_margin(profit_rate: Optional[float]) -> int:
    """
    利润空间评分（权重20%）

    规则:
      毛利率 ≥ 30%   → 100分
      毛利率 ≥ 25%   → 80分
      毛利率 ≥ 20%   → 60分
      毛利率 ≥ 15%   → 40分
      毛利率 ≥ 10%   → 20分
      0% < 毛利率 < 10% → 10分
      毛利率 ≤ 0%    → 0分

    Args:
        profit_rate: 毛利率（小数，如0.25表示25%）
    """
    if profit_rate is None:
        return 40  # 无数据时默认15%左右

    if profit_rate >= 0.30:
        return 100
    elif profit_rate >= 0.25:
        return 80
    elif profit_rate >= 0.20:
        return 60
    elif profit_rate >= 0.15:
        return 40
    elif profit_rate >= 0.10:
        return 20
    elif profit_rate > 0:
        return 10
    else:
        return 0


def _score_life_cycle(life_cycle: Optional[str]) -> int:
    """
    生命周期评分（权重10%）

    规则:
      热卖期(hot)       → 100分
      增长期(growing)   → 90分
      成熟期(mature)    → 70分
      启动期(startup)   → 60分
      下降期(declining) → 30分
      清库存(clearance) → 0分
    """
    score_map = {
        "hot": 100,
        "growing": 90,
        "mature": 70,
        "startup": 60,
        "declining": 30,
        "clearance": 0,
    }
    return score_map.get(life_cycle, 60)


def _score_inventory_urgency(inventory_days: int) -> int:
    """
    库存紧急程度评分（权重10%）

    规则:
      30-90天  → 40分
      15-30天  → 80分
      <15天    → 100分
      91-180天 → 0分
      >180天   → 0分
    """
    if inventory_days < 0:
        return 100
    if 0 <= inventory_days < 15:
        return 100
    if 15 <= inventory_days <= 30:
        return 80
    if 30 < inventory_days <= 90:
        return 40
    return 0


def _score_transport_feasibility(
    transport_mode: str,
    inventory_days: int,
    replenishment_cycle: int,
) -> int:
    """
    运输可达性评分（权重10%）

    规则:
      海运按时到（库存覆盖≥海运时间） → 100分
      空派可达（库存覆盖≥空派时间）   → 80分
      仅快递可达                       → 60分
      无法赶上（任何运输都来不及）     → 0分

    海运时间：淡季30天，旺季45天
    空派时间：淡季10天，旺季15天
    快递时间：淡季3天，旺季6天
    """
    from app.config import settings

    # 使用旺季参数保守估计
    sea_days = settings.SEA_PEAK_DAYS
    air_days = settings.AIR_PEAK_DAYS
    express_days = settings.EXPRESS_PEAK_DAYS

    if inventory_days >= sea_days:
        return 100
    elif inventory_days >= air_days:
        return 80
    elif inventory_days >= express_days:
        return 60
    else:
        return 0


# ── 综合评分 ──────────────────────────────────────────────────


def calculate_total_score(params: dict) -> dict:
    """
    综合评分计算

    6因素加权评分：
      断货风险(25%) + 销量趋势(25%) + 利润空间(20%)
      + 生命周期(10%) + 库存紧急(10%) + 运输可达(10%)

    Args:
        params: 包含以下字段的字典
            - inventory_days: int, 库存覆盖天数
            - replenishment_cycle: int, 补货周期（天）
            - sales_growth_rate: Optional[float], 销售增长率
            - profit_rate: Optional[float], 毛利率
            - life_cycle: Optional[str], 生命周期阶段
            - transport_mode: str, 运输方式
            - inventory_urgency: Optional[int] 可选，若不传则用inventory_days计算

    Returns:
        {
            "total_score": float,       # 综合评分（0-100）
            "level": str,               # 立即采购/观察/暂停
            "details": {
                "stockout_risk": {"score": int, "weight": 0.25, "weighted_score": float},
                "sales_trend": {...},
                "profit_margin": {...},
                "life_cycle": {...},
                "inventory_urgency": {...},
                "transport_feasibility": {...},
            }
        }

    决策等级:
      ≥ 80分 → 立即采购
      60-79分 → 观察
      < 60分 → 暂停采购
    """
    # 提取参数
    inventory_days = params.get("inventory_days", 999)
    replenishment_cycle = params.get("replenishment_cycle", 90)
    sales_growth_rate = params.get("sales_growth_rate")
    profit_rate = params.get("profit_rate")
    life_cycle = params.get("life_cycle")
    transport_mode = params.get("transport_mode", "sea")

    # 计算各因素得分
    factors = {
        "stockout_risk": {
            "score": _score_stockout_risk(inventory_days, replenishment_cycle),
            "weight": 0.25,
            "label": "断货风险",
        },
        "sales_trend": {
            "score": _score_sales_trend(sales_growth_rate),
            "weight": 0.25,
            "label": "销量趋势",
        },
        "profit_margin": {
            "score": _score_profit_margin(profit_rate),
            "weight": 0.20,
            "label": "利润空间",
        },
        "life_cycle": {
            "score": _score_life_cycle(life_cycle),
            "weight": 0.10,
            "label": "生命周期",
        },
        "inventory_urgency": {
            "score": _score_inventory_urgency(inventory_days),
            "weight": 0.10,
            "label": "库存紧急",
        },
        "transport_feasibility": {
            "score": _score_transport_feasibility(transport_mode, inventory_days, replenishment_cycle),
            "weight": 0.10,
            "label": "运输可达",
        },
    }

    # 计算加权总分
    total_score = 0.0
    details = {}

    for key, factor in factors.items():
        weighted = factor["score"] * factor["weight"]
        total_score += weighted
        details[key] = {
            "score": factor["score"],
            "weight": factor["weight"],
            "weighted_score": round(weighted, 2),
        }

    total_score = round(total_score, 2)

    # 决策等级
    if total_score >= 80:
        level = "立即采购"
    elif total_score >= 60:
        level = "观察"
    else:
        level = "暂停"

    logger.info(
        f"采购评分结果: 总分={total_score}, 等级={level}, "
        f"断货风险={details['stockout_risk']['score']}, "
        f"销量趋势={details['sales_trend']['score']}, "
        f"利润空间={details['profit_margin']['score']}, "
        f"生命周期={details['life_cycle']['score']}, "
        f"库存紧急={details['inventory_urgency']['score']}, "
        f"运输可达={details['transport_feasibility']['score']}"
    )

    return {
        "total_score": total_score,
        "level": level,
        "details": details,
    }
