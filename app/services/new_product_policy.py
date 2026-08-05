# -*- coding: utf-8 -*-
"""
新品补货策略模块（需求文档第六章 v2 2026-07-31 版本）

新品只使用本模块规则，老品模块（预测/批次/评分）新品不参与。

流程:
  1. 触发条件: 连续3天或以上日均单量 > 5 单 → 才触发补货计算
  2. 硬性指标: 广告ACOS < 55%（SIF获取），不达标终止
  3. 参考指标: Listing评分/评论（SIF）、产品毛利润/毛利率（领星利润报表）
  4. P0 成本表: Profit为正才可加订（运费/售价影响）
  5. P1 剩余售卖天数: > 14天 才加订，≤ 14天 终止

最高准则:
  - 成本表 Profit 为负数 → 直接终止加订
  - 剩余售卖天数 < 14天 → 直接终止加订

=== 节日/主题产品补货 ===
  需求截止日期 = 节日日期 - 产品销售类型缓冲天数（装饰14/非装饰3）
  补货周期 = 大货生产周期 + 运输时间
  库存售卖天数 = 可使用库存 ÷ 当前生命周期预测日销量
  情况一(预计售罄<补货到货): 空运补缺口 + 海运补货
  情况二(预计售罄≥补货到货): 补货数量 = 销售窗口内每日预测求和 × 安全系数

=== 长期产品补货 ===
  库存售卖天数 = 可使用库存 ÷ 基准销量
  补货数量 = 基准销量 × 补货周期 × 安全系数
  缺货缺口 = (补货到货-预计售罄) × 基准销量 → 空运/快递补缺口
"""

import logging
import math
from datetime import date, datetime, timedelta
from typing import Optional

from app.services.lifecycle import (
    LIFECYCLE_LAUNCH, LIFECYCLE_GROWTH, LIFECYCLE_HOT,
    LIFECYCLE_MATURE, LIFECYCLE_DECLINE, LIFECYCLE_CLEARANCE,
)

logger = logging.getLogger(__name__)

# ==================== 常量 ====================
# 触发条件
TRIGGER_MIN_DAYS = 3          # 连续3天
TRIGGER_MIN_DAILY_ORDER = 5   # 日均单量 > 5

# ACOS 硬性指标（2026-07-31 版本改为 55%）
ACOS_MAX = 0.55               # ACOS < 55%

# 剩余售卖天数门槛
MIN_SELLING_DAYS = 14         # > 14天才可加订

# 节日产品缓冲天数
DECORATION_BUFFER_DAYS = 14   # 装饰类产品: 节日前14天结束售卖
NON_DECORATION_BUFFER_DAYS = 3  # 非装饰/DIY: 节日前3天结束售卖

# 物流淡旺季月份
PEAK_MONTHS = set(range(8, 13))   # 旺季 8-12月
SLOW_MONTHS = set(range(1, 8))    # 淡季 1-7月

# 运输方式配置: 名称 → (淡季天数, 旺季天数, 淡季运费, 旺季运费)
TRANSPORT_MODES = {
    "sea":    {"label": "海运", "slow_days": 30, "peak_days": 45,
               "slow_fee": 17, "peak_fee": 15},
    "air":    {"label": "空派", "slow_days": 10, "peak_days": 15,
               "slow_fee": 70, "peak_fee": 60},
    "express": {"label": "快递", "slow_days": 3, "peak_days": 6,
               "slow_fee": 80, "peak_fee": 70},
}

# 生命周期 → (日均销量系数, 库存安全系数)
LIFECYCLE_FACTORS = {
    LIFECYCLE_LAUNCH: (1.03, 1.0),   # 启动期: 每10天增长3%
    LIFECYCLE_GROWTH: (1.15, 1.15),  # 增长期
    LIFECYCLE_HOT: (1.30, 1.30),     # 热卖期
    LIFECYCLE_MATURE: (1.00, 1.20),  # 成熟期
    LIFECYCLE_DECLINE: (0.70, 1.0),  # 下降期
    LIFECYCLE_CLEARANCE: (0.50, 1.0),  # 清库存期（保守）
}

# 长期产品默认补货周期（按工期）
LONG_TERM_DEFAULT_CYCLE = {
    (1, 10): 45,    # 工期7-10天 → 45天
    (11, 20): 50,   # 工期11-20天 → 50天
    (21, 999): 60,  # 工期21-30天+ → 60天
}


def is_peak_season(today: date = None) -> bool:
    """判断当前是否物流旺季（8-12月）"""
    today = today or date.today()
    return today.month in PEAK_MONTHS


def get_transport_days(mode: str, today: date = None) -> int:
    """获取运输天数（按淡旺季）"""
    cfg = TRANSPORT_MODES.get(mode, TRANSPORT_MODES["sea"])
    return cfg["peak_days"] if is_peak_season(today) else cfg["slow_days"]


def get_transport_fee(mode: str, today: date = None) -> int:
    """获取运输运费（按淡旺季，美元）"""
    cfg = TRANSPORT_MODES.get(mode, TRANSPORT_MODES["sea"])
    return cfg["peak_fee"] if is_peak_season(today) else cfg["slow_fee"]


def check_trigger(daily_orders: list) -> bool:
    """
    检查是否满足补货触发条件

    连续3天或以上，日均单量超5单。

    Args:
        daily_orders: 最近N天每日订单量列表（最新在最后）

    Returns:
        bool: 是否触发补货计算
    """
    if len(daily_orders) < TRIGGER_MIN_DAYS:
        return False

    recent = daily_orders[-TRIGGER_MIN_DAYS:]
    avg = sum(recent) / len(recent)
    triggered = avg > TRIGGER_MIN_DAILY_ORDER
    logger.info(f"新品触发检查: 最近{TRIGGER_MIN_DAYS}天单量={recent}, "
                f"日均={avg:.1f}, 阈值>{TRIGGER_MIN_DAILY_ORDER}, "
                f"{'触发' if triggered else '未触发'}")
    return triggered


def check_acos(acos: Optional[float]) -> tuple:
    """
    检查广告ACOS是否达标（硬性指标，阈值55%）

    Args:
        acos: 最近30天整体ACOS（小数，如0.45）

    Returns:
        (达标bool, 原因str)
    """
    if acos is None:
        return False, "无ACOS数据，无法确认达标"
    if acos < ACOS_MAX:
        return True, f"ACOS={acos:.1%} < 55%，达标"
    return False, f"ACOS={acos:.1%} ≥ 55%，不达标，终止加订"


def check_profit(profit: Optional[float]) -> tuple:
    """
    检查成本表 Profit 是否盈利（P0）

    最高准则: Profit为负 → 直接终止加订

    Args:
        profit: 单件利润（美元）

    Returns:
        (可加订bool, 原因str)
    """
    if profit is None:
        return True, "无成本表数据，跳过Profit检查"
    if profit < 0:
        return False, f"成本表Profit为负({profit:.2f})，终止加订"
    return True, f"成本表Profit={profit:.2f}，有盈利，可加订"


def check_remaining_days(remaining_days: Optional[int]) -> tuple:
    """
    检查剩余售卖天数（P1）

    最高准则: 剩余售卖天数 < 14天 → 直接终止加订

    Args:
        remaining_days: 剩余可售卖天数

    Returns:
        (可加订bool, 原因str)
    """
    if remaining_days is None:
        return True, "无剩余售卖天数数据，跳过检查"
    if remaining_days < MIN_SELLING_DAYS:
        return False, f"剩余售卖天数={remaining_days}天 < {MIN_SELLING_DAYS}天，终止加订"
    return True, f"剩余售卖天数={remaining_days}天 > {MIN_SELLING_DAYS}天，可加订"


# ==================== 节日/主题产品 ====================

def calc_demand_deadline(festival_date: date, is_decoration: bool) -> date:
    """
    计算需求截止日期

    需求截止日期 = 节日日期 - 产品销售类型缓冲天数
    装饰类: 14天 / 非装饰(DIY): 3天

    Args:
        festival_date: 节日日期
        is_decoration: 是否装饰类产品

    Returns:
        需求截止日期
    """
    buffer_days = DECORATION_BUFFER_DAYS if is_decoration else NON_DECORATION_BUFFER_DAYS
    deadline = festival_date - timedelta(days=buffer_days)
    logger.info(f"需求截止日: 节日={festival_date}, 类型={'装饰' if is_decoration else '非装饰'}, "
                f"缓冲={buffer_days}天, 截止={deadline}")
    return deadline


def calc_arrival_date(current_date: date, lead_time: int, mode: str) -> date:
    """计算补货到货日期 = 当前日期 + 补货周期(工期+运输)"""
    transport_days = get_transport_days(mode, current_date)
    arrival = current_date + timedelta(days=lead_time + transport_days)
    logger.info(f"到货日计算: 当前={current_date}, 工期={lead_time}, 运输={transport_days}天({mode}), 到货={arrival}")
    return arrival


def calc_inventory_sellout_days(available_stock: int, base_daily_sales: float,
                                lifecycle: str) -> float:
    """
    计算库存售卖天数

    库存售卖天数 = 可使用库存 ÷ 当前生命周期预测日销量

    注意: 安全系数只用于补货计算，不用于库存消耗预测。

    Args:
        available_stock: 可使用库存(FBA可售+预留+在途)
        base_daily_sales: 基准销量（最近3天平均）
        lifecycle: 生命周期阶段

    Returns:
        库存售卖天数
    """
    factor, _ = LIFECYCLE_FACTORS.get(lifecycle, (1.0, 1.2))
    daily = base_daily_sales * factor
    if daily <= 0:
        return 999
    days = available_stock / daily
    logger.info(f"库存售卖测算: 库存={available_stock}, 基准={base_daily_sales}, "
                f"阶段系数={factor}, 预测日均={daily:.1f}, 可卖={days:.1f}天")
    return days


def daily_forecast_by_stage(base_daily_sales: float, lifecycle: str,
                            days: int, launch_days: int = 0) -> float:
    """
    按生命周期阶段计算某天的预测销量

    Args:
        base_daily_sales: 基准销量（最近3天平均单量）
        lifecycle: 生命周期阶段
        days: 预测天数
        launch_days: 距离启动的天数（启动期增长用）

    Returns:
        预测日均销量
    """
    factor, _ = LIFECYCLE_FACTORS.get(lifecycle, (1.0, 1.2))

    if lifecycle == LIFECYCLE_LAUNCH:
        # 启动期: 每10天增长约3%
        return base_daily_sales * (1.03 ** (max(launch_days, 1) / 10))
    return base_daily_sales * factor


def forecast_window_demand(base_daily_sales: float, lifecycle: str,
                           start: date, end: date, safety_factor: float = None) -> int:
    """
    计算销售窗口内的总预测需求

    补货数量 = ∑(窗口内每日预测销量) × 库存安全系数

    Args:
        base_daily_sales: 基准销量
        lifecycle: 生命周期阶段
        start: 窗口开始日期（补货到货日）
        end: 窗口结束日期（需求截止日）
        safety_factor: 安全系数（默认取生命周期配置）

    Returns:
        总预测需求
    """
    days = (end - start).days
    if days <= 0:
        return 0

    if safety_factor is None:
        _, safety_factor = LIFECYCLE_FACTORS.get(lifecycle, (1.0, 1.2))

    # 每日预测求和
    total = 0.0
    for d in range(days):
        daily = daily_forecast_by_stage(base_daily_sales, lifecycle, days, d)
        total += daily

    demand = round(total * safety_factor)
    logger.info(f"窗口需求: 基准={base_daily_sales}, 阶段={lifecycle}, "
                f"窗口={start}~{end}({days}天), 安全系数={safety_factor}, 需求={demand}")
    return demand


def festival_replenishment_plan(
    base_daily_sales: float,
    lifecycle: str,
    available_stock: int,
    lead_time: int,
    festival_date: date,
    is_decoration: bool,
    current_date: date = None,
) -> dict:
    """
    节日/主题产品补货计划（需求文档第六章）

    流程:
      1. 计算需求截止日期
      2. 计算库存售卖天数/售罄日期
      3. 计算各运输方式到货日期
      4. 情况一: 库存无法支撑补货周期 → 空运补缺口 + 海运补货
      5. 情况二: 库存可支撑 → 补货数量 = 窗口内预测求和 × 安全系数

    Args:
        base_daily_sales: 基准销量（最近3天平均）
        lifecycle: 生命周期阶段
        available_stock: 可使用库存
        lead_time: 大货工期
        festival_date: 节日日期
        is_decoration: 是否装饰类
        current_date: 当前日期

    Returns:
        {
            "demand_deadline": str,     # 需求截止日期
            "sellout_date": str,        # 预计售罄日期
            "sellout_days": float,      # 库存售卖天数
            "transport_plans": [        # 各运输方式方案
                {"mode": str, "label": str, "arrival": str, "selling_days": int,
                 "qty": int, "gap_qty": int, "is_profitable_first": bool, "note": str}
            ],
            "recommended": {...},       # 推荐方案
            "can_replenish": bool,
            "reason": str,
        }
    """
    current_date = current_date or date.today()

    # 1. 需求截止日期
    demand_deadline = calc_demand_deadline(festival_date, is_decoration)

    # 2. 库存售卖天数
    sellout_days = calc_inventory_sellout_days(available_stock, base_daily_sales, lifecycle)
    sellout_date = current_date + timedelta(days=sellout_days)

    # 3. 各运输方式方案
    plans = []
    for mode, cfg in TRANSPORT_MODES.items():
        arrival = calc_arrival_date(current_date, lead_time, mode)
        selling_days = (demand_deadline - arrival).days

        # 剩余售卖天数检查（P1）
        ok_remaining, reason_remaining = check_remaining_days(selling_days)

        # 情况判断
        if sellout_date < arrival:
            # 情况一: 库存无法支撑 → 存在缺口
            gap_days = (arrival - sellout_date).days
            gap_qty = round(gap_days * daily_forecast_by_stage(base_daily_sales, lifecycle, gap_days))
            # 海运正常补货（窗口内预测×安全系数）
            sea_qty = forecast_window_demand(base_daily_sales, lifecycle,
                                             arrival, demand_deadline)
            qty = sea_qty
            note = (f"库存预计{sellout_date}售罄，存在{gap_days}天缺口，"
                    f"需空运/快递补缺口{gap_qty}件，本渠道补货{qty}件")
        else:
            # 情况二: 库存可支撑补货周期
            gap_qty = 0
            qty = forecast_window_demand(base_daily_sales, lifecycle,
                                         arrival, demand_deadline)
            note = f"库存可支撑至{sellout_date}，本渠道窗口{selling_days}天，补货{qty}件"

        if not ok_remaining:
            qty = 0
            note = reason_remaining

        plans.append({
            "mode": mode,
            "label": cfg["label"],
            "arrival": arrival.isoformat(),
            "selling_days": max(selling_days, 0),
            "qty": qty,
            "gap_qty": gap_qty,
            "transport_days": get_transport_days(mode, current_date),
            "freight_fee": get_transport_fee(mode, current_date),
            "note": note,
        })

    # 4. 推荐方案: 优先海运（成本低），缺口用空运/快递
    recommended = None
    can_replenish = False
    for p in plans:
        if p["qty"] > 0 and p["selling_days"] >= MIN_SELLING_DAYS:
            if recommended is None or p["mode"] == "sea":
                recommended = p
                can_replenish = True

    if not recommended:
        reason = "所有运输方式均无法满足剩余售卖天数≥14天或存在Profit风险"
    else:
        reason = recommended["note"]

    return {
        "demand_deadline": demand_deadline.isoformat(),
        "sellout_date": sellout_date.isoformat(),
        "sellout_days": round(sellout_days, 1),
        "transport_plans": plans,
        "recommended": recommended,
        "can_replenish": can_replenish,
        "reason": reason,
    }


# ==================== 长期产品 ====================

def long_term_replenishment_plan(
    base_daily_sales: float,
    available_stock: int,
    lead_time: int,
    safety_factor: float = 1.2,
    current_date: date = None,
) -> dict:
    """
    长期产品补货计划（需求文档第六章 长期产品）

    流程:
      1. 库存售卖天数 = 可用库存 ÷ 基准销量
      2. 补货周期 = 大货工期 + 运输时间（默认海运）
      3. 补货数量 = 基准销量 × 补货周期 × 安全系数
      4. 缺货风险: 缺口 = (到货-售罄) × 基准销量 → 空运/快递补缺口

    Args:
        base_daily_sales: 基准销量（最近3天平均）
        available_stock: 可使用库存
        lead_time: 大货工期
        safety_factor: 安全系数（默认1.2）
        current_date: 当前日期

    Returns:
        {
            "sellout_days": float,     # 库存售卖天数
            "sellout_date": str,       # 预计售罄日期
            "replenishment_cycle": int,# 补货周期
            "base_qty": int,           # 基础补货数量
            "final_qty": int,          # 最终补货数量(含安全系数)
            "gap_qty": int,            # 缺货缺口
            "gap_mode": str,           # 补缺口运输方式
            "can_replenish": bool,
            "reason": str,
        }
    """
    current_date = current_date or date.today()

    # 1. 库存售卖天数（长期产品用基准销量，无阶段系数）
    sellout_days = available_stock / base_daily_sales if base_daily_sales > 0 else 999
    sellout_date = current_date + timedelta(days=sellout_days)

    # 2. 补货周期（按工期取默认值: 7-10天→45, 11-20天→50, 21天+→60）
    #    文档: 默认补货周期已含生产+运输，直接使用
    replenishment_cycle = LONG_TERM_DEFAULT_CYCLE.get(
        next(((lo, hi) for lo, hi in LONG_TERM_DEFAULT_CYCLE if lo <= lead_time <= hi), (1, 10)),
        45,
    )

    # 3. 补货数量 = 基准 × 补货周期 × 安全系数
    base_qty = round(base_daily_sales * replenishment_cycle)
    final_qty = round(base_qty * safety_factor)

    # 4. 缺货风险判断
    arrival = current_date + timedelta(days=replenishment_cycle)
    gap_qty = 0
    gap_mode = ""
    if sellout_date < arrival:
        gap_days = (arrival - sellout_date).days
        gap_qty = round(gap_days * base_daily_sales)
        # 判断空运/快递是否能补上
        for mode in ("air", "express"):
            if current_date + timedelta(days=lead_time + get_transport_days(mode, current_date)) <= sellout_date:
                gap_mode = TRANSPORT_MODES[mode]["label"]
                break
        if not gap_mode:
            gap_mode = "无法弥补"

    can_replenish = gap_qty == 0 or bool(gap_mode and gap_mode != "无法弥补")
    reason = (f"库存售卖{round(sellout_days, 1)}天(至{sellout_date}), "
              f"默认补货周期{replenishment_cycle}天, "
              f"基础补货{base_qty}, 含安全系数{final_qty}")

    return {
        "sellout_days": round(sellout_days, 1),
        "sellout_date": sellout_date.isoformat(),
        "replenishment_cycle": replenishment_cycle,
        "base_qty": base_qty,
        "final_qty": final_qty,
        "gap_qty": gap_qty,
        "gap_mode": gap_mode,
        "can_replenish": can_replenish,
        "reason": reason,
    }


# 兼容旧接口: 简化版补货数量计算
def forecast_stage_demand(base_daily_sales: float, lifecycle: str,
                          days: int, launch_days: int = 0) -> float:
    """计算剩余销售周期内的总预测需求（简化版，兼容调用）"""
    if days <= 0:
        return 0
    _, safety_factor = LIFECYCLE_FACTORS.get(lifecycle, (1.0, 1.2))
    total = sum(
        daily_forecast_by_stage(base_daily_sales, lifecycle, days, launch_days + d)
        for d in range(days)
    )
    return round(total * safety_factor)


def forecast_long_term_demand(base_daily_sales: float, lead_time: int,
                              safety_factor: float = 1.2) -> int:
    """长期产品补货数量（简化版: 基准×默认周期×安全系数）"""
    cycle = LONG_TERM_DEFAULT_CYCLE.get(
        next(((lo, hi) for lo, hi in LONG_TERM_DEFAULT_CYCLE if lo <= lead_time <= hi), (1, 10)),
        45,
    )
    return round(base_daily_sales * cycle * safety_factor)


def round_to_box(qty: int, box_qty: int = 1) -> int:
    """按装箱数量向上取整"""
    if box_qty <= 0:
        return qty
    return int(math.ceil(qty / box_qty) * box_qty)
