"""
采购数量和批次规划模块

提供建议采购数量计算和批次规划功能。
"""

import math
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def calculate_suggested_qty(
    forecast_demand: int,
    safe_stock: int,
    available_stock: int,
    box_qty: int,
    min_order_qty: int,
) -> int:
    """
    计算建议采购数量

    建议数量 = 未来需求 + 安全库存 - 当前可用库存
    最终数量 = 向上取整至(装箱数量倍数) 且 ≥ 最低采购量

    Args:
        forecast_demand: 未来N月预测需求总量
        safe_stock: 安全库存数量
        available_stock: 当前可用库存
        box_qty: 单箱数量（装箱数量倍数）
        min_order_qty: 最低采购量

    Returns:
        最终建议采购数量
    """
    # 基础建议量
    suggested = forecast_demand + safe_stock - available_stock

    # 如果建议量 <= 0，则无需采购
    if suggested <= 0:
        logger.info(
            f"建议采购量={suggested}（需求={forecast_demand}, "
            f"安全库存={safe_stock}, 可用={available_stock}）, 无需采购"
        )
        return 0

    # 向上取整至装箱数量倍数
    if box_qty > 0:
        suggested = math.ceil(suggested / box_qty) * box_qty

    # 不低于最低采购量
    if min_order_qty > 0 and suggested < min_order_qty:
        suggested = min_order_qty

    logger.info(
        f"建议采购量={suggested}（需求={forecast_demand}, "
        f"安全库存={safe_stock}, 可用={available_stock}, "
        f"装箱量={box_qty}, 最低={min_order_qty})"
    )
    return suggested


def plan_batches(
    total_qty: int,
    is_seasonal: bool = False,
    sales_trend: str = "stable",
    life_cycle: str = "mature",
    supply_cycle: int = 30,
) -> List[dict]:
    """
    采购批次规划

    根据产品属性（季节性、销售趋势、生命周期、供应周期）
    决定是否分批次采购以及各批次的数量分配。

    Args:
        total_qty: 总采购数量
        is_seasonal: 是否为季节性产品
        sales_trend: 销售趋势（growing/stable/declining）
        life_cycle: 生命周期阶段（hot/growing/mature/declining/startup/clearance）
        supply_cycle: 供应周期（天）

    Returns:
        批次规划列表，如：
        [
            {"batch": 1, "qty": 9000, "ratio": 0.6, "note": "首批60%"},
            {"batch": 2, "qty": 6000, "ratio": 0.4, "note": "第二批40%，30天后"}
        ]

    Notes:
        - 季节性产品且总数量较大时（>1000），分多批采购
        - 销售趋势增长时，首批可适当提高比例
        - 销售趋势下降时，降低首批比例
        - 供应周期短则分批意义不大
    """
    if total_qty <= 0:
        return []

    batches = []

    # 判断是否需要分批
    should_split = False

    # 季节性产品且数量较大
    if is_seasonal and total_qty > 1000:
        should_split = True

    # 增长趋势且供应周期较长
    if sales_trend == "growing" and supply_cycle > 20:
        should_split = True

    # 热卖期/增长期产品
    if life_cycle in ("hot", "growing") and total_qty > 2000:
        should_split = True

    if not should_split:
        # 单批次
        batches.append({
            "batch": 1,
            "qty": total_qty,
            "ratio": 1.0,
            "note": "单批次采购",
        })
        logger.info(f"单批次采购: total_qty={total_qty}")
        return batches

    # 多批次规划逻辑（需求文档第十二章：可拆3批+备用）
    if total_qty > 5000:
        # 大批量（>5000）: 3批 + 备用
        if sales_trend == "growing":
            ratios = [0.30, 0.25, 0.20, 0.25]  # 首批30%，后续25%/20%，备用25%
            notes = ["首批30%，快速抢占", "第二批25%，30天后",
                     "第三批20%，60天后", "备用25%，视销量追加"]
        elif sales_trend == "declining":
            ratios = [0.20, 0.15, 0.15, 0.50]  # 首批20%，控制风险，备用50%
            notes = ["首批20%，控制风险", "第二批15%", "第三批15%", "备用50%，视销售决定"]
        else:
            ratios = [0.25, 0.20, 0.15, 0.40]  # 稳定/季节性
            notes = ["首批25%", "第二批20%", "第三批15%", "备用40%，视销售决定"]
    elif sales_trend == "growing":
        # 增长趋势：首批60%，第二批40%
        ratios = [0.6, 0.4]
        notes = ["首批60%，满足快速增长需求", "第二批40%，根据销售情况调整"]
    elif sales_trend == "declining":
        # 下降趋势：首批40%，第二批60%（降低风险）
        ratios = [0.4, 0.6]
        notes = ["首批40%，控制风险", "第二批60%，视销售情况再决定"]
    else:
        # 稳定趋势或季节性：首批50%，第二批50%
        ratios = [0.5, 0.5]
        notes = ["首批50%", "第二批50%，视销售情况调整"]

    # 计算各批次数量
    remaining = total_qty
    for i, (ratio, note) in enumerate(zip(ratios, notes)):
        is_last = (i == len(ratios) - 1)
        if is_last:
            qty = remaining
        else:
            qty = _round_to_box(total_qty * ratio)
            remaining -= qty

        if qty > 0:
            batches.append({
                "batch": i + 1,
                "qty": qty,
                "ratio": round(ratio, 2),
                "note": note,
            })

    logger.info(
        f"多批次采购: total_qty={total_qty}, "
        f"批次={len(batches)}, 首批={batches[0]['qty'] if batches else 0}"
    )
    return batches


def _round_to_box(qty: float, box_qty: int = 1) -> int:
    """按装箱数量向上取整（内部辅助函数）"""
    if box_qty <= 0:
        return round(qty)
    return int(math.ceil(qty / box_qty) * box_qty)
